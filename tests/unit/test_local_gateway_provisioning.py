"""Synthetic-only no-TTY coordinator tests; no real AP, D-Bus or sockets."""
import asyncio
import base64
import json
import os
from pathlib import Path
import sqlite3

import pytest

from ai_cat_controller.local_gateway.provisioning import (
    LocalProvisioningCoordinator, LocalProvisioningError,
)
from ai_cat_controller.provisioning.protocol import Invitation, decode_packet, encode_packet, parse_json
from ai_cat_controller.local_gateway.provision_reviews import ACKNOWLEDGEMENTS, canonical

DEVICE = 'dev_' + 'a' * 32
OPEN = 'open-local-provisioning'
REVIEW_IDENTITY = {'cardId': 'b' * 32, 'userId': 'user_synthetic', 'bindingEpoch': '2026-11-02T00:00:00+00:00'}


class Clock:
    def __init__(self):
        self.now = 1000.0
    def __call__(self):
        return self.now


def fixture(tmp_path, *, result='connected', configure=True, drained=True,
            begin_error=False, close_error=False, gated_apply=False):
    calls, rows = [], {}
    began, server_ready, applied, release = (asyncio.Event() for _ in range(4))
    clock = Clock()
    class Backend:
        async def begin(self, name, password, *, timeout):
            assert name.startswith('AI-CAT-') and len(name) == 13
            assert password == '88888888' and timeout == 180
            calls.append('begin'); began.set()
            if begin_error:
                raise RuntimeError('synthetic secret that must not escape')
        async def __call__(self, credentials, request_id):
            assert credentials.ssid == '合成网络' and credentials.password == 'synthetic-wifi-password'
            assert request_id == 'wifi_synthetic'
            calls.append('apply'); applied.set()
            if gated_apply:
                await release.wait()
            return result
        async def close(self):
            calls.append('backend-close')
            if close_error:
                raise RuntimeError('synthetic close secret')
    class Server:
        def __init__(self, window):
            rows['window'] = self.window = window
        async def start(self):
            calls.append('server-start'); server_ready.set()
            if configure:
                invite = Invitation.parse(rows['prepared']['invitation'])
                packet = encode_packet(invite, 'wifi_synthetic', 'configure',
                    {'ssid': '合成网络', 'password': 'synthetic-wifi-password', 'security': 'wpa2-personal'}, direction='request')
                await self.window.process_packet(packet)
                if drained:
                    self.window.start_pending('wifi_synthetic')
        async def close(self):
            calls.append('server-close')
            await self.window.close()
    options = dict(db_path=tmp_path / 'private' / 'provision.sqlite3', device_id=DEVICE,
                   backend_factory=Backend, server_factory=Server, clock=clock, monotonic=clock)
    return calls, rows, clock, (began, server_ready, applied, release), options


def plain_receipt(prepared, receipt):
    value = receipt['receiptQr'].split('AICAT-PROVISION-RECEIPT:1:', 1)[1]
    packet = parse_json(base64.urlsafe_b64decode(value + '=' * (-len(value) % 4)))
    _, body = decode_packet(Invitation.parse(prepared['invitation']), packet, direction='response')
    assert packet['requestId'] == 'wifi_synthetic'
    return body


@pytest.mark.asyncio
@pytest.mark.parametrize('result', ['connected', 'failed', 'unknown', 'not_sent'])
async def test_fixed_audio_cues_do_not_replace_authenticated_network_result(tmp_path, result):
    _, rows, _, _, options = fixture(tmp_path, result=result)
    cues = []
    async def notify(event):
        cues.append(event)
    coordinator = LocalProvisioningCoordinator(**options, notify=notify)
    rows['prepared'] = await coordinator.prepare('outer_cues', confirmation=OPEN)
    assert cues == []  # prepare is not an open hotspot.
    terminal = await coordinator.start('outer_cues')
    assert terminal['status'] == result
    assert plain_receipt(rows['prepared'], terminal) == {'status': result}
    assert sorted(cues) == sorted(['open', 'connecting', result])
    await coordinator.start('outer_cues')
    await coordinator.get_receipt('outer_cues')
    assert len(cues) == 3  # replays/read-only polling never replay sound.
    await coordinator.aclose()


@pytest.mark.asyncio
async def test_audio_failure_does_not_fail_network_or_log_private_error(tmp_path, caplog):
    _, rows, _, _, options = fixture(tmp_path)
    async def notify(event):
        raise OSError('PRIVATE_AUDIO_DETAIL')
    coordinator = LocalProvisioningCoordinator(**options, notify=notify)
    rows['prepared'] = await coordinator.prepare('outer_cues', confirmation=OPEN)
    terminal = await coordinator.start('outer_cues')
    assert terminal['status'] == 'connected'
    assert 'PRIVATE_AUDIO_DETAIL' not in caplog.text
    assert 'Local network cue unavailable' in caplog.text
    await coordinator.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('diagnostic,expected', [
    ({'stage': 'PERSIST', 'cause': 'TIMEOUT', 'elapsed_ms': 8000, 'rollback': 'SUCCESS'}, True),
    ({'stage': 'PRIVATE_STAGE', 'cause': 'TIMEOUT', 'elapsed_ms': 8000, 'rollback': 'SUCCESS'}, False),
    ({'stage': 'PERSIST', 'cause': 'TIMEOUT', 'elapsed_ms': 8000, 'rollback': 'SUCCESS', 'password': 'PRIVATE_PASSWORD'}, False),
    (None, False),
])
async def test_unknown_backend_diagnostic_is_fixed_safe_journal_metadata_not_a_result_change(tmp_path, caplog, diagnostic, expected):
    _, rows, _, _, options = fixture(tmp_path, result='unknown')
    original = options['backend_factory']
    class Backend(original):
        @property
        def last_diagnostic(self):
            return diagnostic
    options['backend_factory'] = Backend
    coordinator = LocalProvisioningCoordinator(**options)
    rows['prepared'] = await coordinator.prepare('outer_diagnostic', confirmation=OPEN)
    terminal = await coordinator.start('outer_diagnostic')
    assert terminal['status'] == 'unknown'
    assert plain_receipt(rows['prepared'], terminal) == {'status': 'unknown'}
    assert ('stage=PERSIST cause=TIMEOUT elapsed_ms=8000 rollback=SUCCESS' in caplog.text) is expected
    assert all(value not in caplog.text for value in ('PRIVATE_', '合成网络', 'synthetic-wifi-password', 'AICAT-PROVISION', 'wifi_synthetic', DEVICE))
    count = len(caplog.records)
    assert (await coordinator.current_window())['window']['status'] == 'unknown'
    assert (await coordinator.start('outer_diagnostic'))['status'] == 'unknown'
    assert len(caplog.records) == count
    await coordinator.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['getter', 'logger'])
async def test_optional_backend_getter_or_logging_failure_cannot_replace_network_result(tmp_path, monkeypatch, failure):
    _, rows, _, _, options = fixture(tmp_path, result='failed')
    original = options['backend_factory']
    class Backend(original):
        @property
        def last_diagnostic(self):
            if failure == 'getter':
                raise RuntimeError('PRIVATE_DIAGNOSTIC_GETTER')
            return {'stage': 'PERSIST', 'cause': 'TIMEOUT', 'elapsed_ms': 8000, 'rollback': 'SUCCESS'}
    options['backend_factory'] = Backend
    def broken_log(*args, **kwargs):
        raise RuntimeError('PRIVATE_LOG_HANDLER')
    monkeypatch.setattr('ai_cat_controller.local_gateway.provisioning.LOGGER.warning', broken_log)
    coordinator = LocalProvisioningCoordinator(**options)
    rows['prepared'] = await coordinator.prepare('outer_bad_diagnostic', confirmation=OPEN)
    terminal = await coordinator.start('outer_bad_diagnostic')
    assert terminal['status'] == 'failed'
    assert plain_receipt(rows['prepared'], terminal) == {'status': 'failed'}
    await coordinator.aclose()


@pytest.mark.asyncio
async def test_fixed_cue_wait_and_bounded_reap_fit_checkpoint_settlement_allowance(tmp_path, monkeypatch):
    from ai_cat_controller.provisioning.nm_backend import CUE_SETTLE_SECONDS
    from ai_cat_controller.local_gateway.network_cues import REAP_TIMEOUT
    _, rows, _, _, options = fixture(tmp_path)
    waits = []
    real_wait = asyncio.wait_for
    async def bounded_wait(awaitable, timeout):
        waits.append(timeout)
        return await real_wait(awaitable, timeout=timeout)
    async def notify(event):
        return True
    monkeypatch.setattr('ai_cat_controller.local_gateway.provisioning.asyncio.wait_for', bounded_wait)
    coordinator = LocalProvisioningCoordinator(**options, notify=notify)
    rows['prepared'] = await coordinator.prepare('outer_cue_budget', confirmation=OPEN)
    assert (await coordinator.start('outer_cue_budget'))['status'] == 'connected'
    assert waits == [2.5] * 3
    assert max(waits) + REAP_TIMEOUT <= CUE_SETTLE_SECONDS
    await coordinator.aclose()


async def review_fixture(tmp_path, *, result='unknown', after_settlement=True):
    calls, rows, clock, events, options = fixture(tmp_path, result=result)
    options['review_identity'] = dict(REVIEW_IDENTITY)
    coordinator = LocalProvisioningCoordinator(**options)
    rows['prepared'] = await coordinator.prepare('outer_review', confirmation=OPEN)
    terminal = await coordinator.start('outer_review')
    if after_settlement:
        clock.now = rows['prepared']['expiresAt'] + 90
    args = {'window_id': terminal['windowId'], 'network_request_id': terminal['networkRequestId'],
            'acks': list(ACKNOWLEDGEMENTS)}
    return coordinator, calls, rows, clock, options, args


async def commit_review(coordinator, args, *, guard=lambda: True):
    prepared = await coordinator.prepare_archive('outer_review', **args)
    result = await coordinator.confirm_archive('outer_review', review_token=prepared['reviewToken'], guard=guard, **args)
    return prepared, result


@pytest.mark.asyncio
async def test_review_status_is_readonly_and_requires_exact_original_unknown_receipt_after_settlement(tmp_path):
    coordinator, calls, rows, clock, options, args = await review_fixture(tmp_path, after_settlement=False)
    before = coordinator._row('outer_review')
    files = set(options['db_path'].parent.iterdir())
    status = await coordinator.archive_status('outer_review')
    assert set(status) == {'eligible','cardId','original','status','resolution','reviewId','archivedAt','acknowledgements'}
    assert status == {'eligible': False, 'cardId': REVIEW_IDENTITY['cardId'],
        'original': {'deviceId': DEVICE, 'requestId': 'outer_review', 'windowId': before['window_id'], 'networkRequestId': 'wifi_synthetic'},
        'status': 'unknown','resolution':'pending','reviewId':None,'archivedAt':None,'acknowledgements':[]}
    with pytest.raises(LocalProvisioningError):
        await coordinator.prepare_archive('outer_review', **args)
    clock.now = before['deadline'] + 90
    assert (await coordinator.archive_status('outer_review'))['eligible'] is True
    assert coordinator._row('outer_review') == before
    assert set(options['db_path'].parent.iterdir()) == files
    assert calls.count('apply') == 1
    await coordinator.aclose()


@pytest.mark.asyncio
async def test_explicit_review_appends_once_without_changing_original_ciphertext_then_only_new_window_is_allowed(tmp_path):
    coordinator, calls, rows, clock, options, args = await review_fixture(tmp_path)
    original = coordinator._row('outer_review')
    prepared, result = await commit_review(coordinator, args)
    assert set(prepared) == {'reviewToken','expiresAt','reviewId','cardId','original'}
    assert prepared['expiresAt'] == clock.now + 30
    assert result['resolution'] == 'operator_archived' and result['status'] == 'unknown' and result['eligible'] is False
    assert result['reviewId'] == prepared['reviewId'] and len(result['reviewId']) == 32
    assert result['archivedAt'] == int(clock.now * 1000) and result['acknowledgements'] == ACKNOWLEDGEMENTS
    directory = options['db_path'].parent / 'provision-reviews'
    audit = next(directory.iterdir())
    assert directory.stat().st_mode & 0o777 == 0o700 and audit.stat().st_mode & 0o777 == 0o600
    raw = audit.read_bytes()
    assert all(secret not in raw.decode() for secret in ('AICAT-PROVISION:', 'AICAT-PROVISION-RECEIPT:', 'synthetic-wifi-password', '合成网络', prepared['reviewToken']))
    assert coordinator._row('outer_review') == original
    assert (await coordinator.resume('outer_review'))['receiptQr'] == original['receipt_qr']
    assert (await coordinator.resume('outer_review'))['invitation'] == original['invitation']
    duplicate = await coordinator.confirm_archive('outer_review', review_token=prepared['reviewToken'], guard=lambda: True, **args)
    assert duplicate == result and audit.read_bytes() == raw and len(list(directory.iterdir())) == 1
    with pytest.raises(LocalProvisioningError):
        await coordinator.start('outer_review')
    next_window = await coordinator.prepare('outer_new_window', confirmation=OPEN)
    assert next_window['windowId'] != original['window_id'] and next_window['status'] == 'prepared'
    assert calls.count('begin') == calls.count('apply') == 1
    await coordinator.aclose()
    restored = LocalProvisioningCoordinator(**options)
    assert await restored.archive_status('outer_review') == result
    assert restored._row('outer_review') == original
    with pytest.raises(LocalProvisioningError):
        await restored.confirm_archive('outer_review', review_token=prepared['reviewToken'], guard=lambda: True, **args)
    assert (await restored.get_receipt('outer_review'))['receiptQr'] == original['receipt_qr']
    await restored.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('bad', ['wrong-window','wrong-inner','missing-ack','duplicate-ack','unknown-ack','forged-token','false-guard','broken-guard'])
async def test_review_rejects_identity_ack_token_and_guard_changes_without_any_audit_or_network(tmp_path, bad):
    coordinator, calls, rows, clock, options, args = await review_fixture(tmp_path)
    before = coordinator._row('outer_review')
    prepared = await coordinator.prepare_archive('outer_review', **args)
    changed = dict(args)
    if bad == 'wrong-window': changed['window_id'] = 'c' * 32
    if bad == 'wrong-inner': changed['network_request_id'] = 'wifi_wrong'
    if bad == 'missing-ack': changed['acks'] = ACKNOWLEDGEMENTS[:-1]
    if bad == 'duplicate-ack': changed['acks'] = [ACKNOWLEDGEMENTS[0]] * 4
    if bad == 'unknown-ack': changed['acks'] = ACKNOWLEDGEMENTS[:-1] + ['PRIVATE_ACK']
    token = 'forged' if bad == 'forged-token' else prepared['reviewToken']
    def guard():
        if bad == 'broken-guard': raise RuntimeError('PRIVATE_GUARD')
        return bad != 'false-guard'
    with pytest.raises(LocalProvisioningError):
        await coordinator.confirm_archive('outer_review', review_token=token, guard=guard, **changed)
    assert coordinator._row('outer_review') == before
    assert not (options['db_path'].parent / 'provision-reviews').exists()
    assert calls.count('apply') == 1
    await coordinator.aclose()


@pytest.mark.asyncio
async def test_review_intent_blocks_parallel_prepare_and_status_until_expiry_then_late_confirmation_cannot_write(tmp_path):
    coordinator, calls, rows, clock, options, args = await review_fixture(tmp_path)
    prepared = await coordinator.prepare_archive('outer_review', **args)
    assert (await coordinator.archive_status('outer_review'))['eligible'] is False
    with pytest.raises(LocalProvisioningError):
        await coordinator.prepare_archive('outer_review', **args)
    clock.now += 30
    assert (await coordinator.archive_status('outer_review'))['eligible'] is True
    with pytest.raises(LocalProvisioningError):
        await coordinator.confirm_archive('outer_review', review_token=prepared['reviewToken'], guard=lambda: True, **args)
    fresh = await coordinator.prepare_archive('outer_review', **args)
    assert fresh['reviewId'] != prepared['reviewId']
    with pytest.raises(LocalProvisioningError):
        await coordinator.confirm_archive('outer_review', review_token=prepared['reviewToken'], guard=lambda: True, **args)
    assert not (options['db_path'].parent / 'provision-reviews').exists()
    await coordinator.aclose()


@pytest.mark.asyncio
async def test_guard_wait_rechecks_same_source_expiry_and_closing_before_append(tmp_path):
    coordinator, calls, rows, clock, options, args = await review_fixture(tmp_path)
    prepared = await coordinator.prepare_archive('outer_review', **args)
    async def guard():
        clock.now += 30
        return True
    with pytest.raises(LocalProvisioningError):
        await coordinator.confirm_archive('outer_review', review_token=prepared['reviewToken'], guard=guard, **args)
    assert not (options['db_path'].parent / 'provision-reviews').exists()
    await coordinator.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['content','truncate','permissions','symlink','hardlink','delete'])
async def test_review_corruption_is_latched_and_cannot_open_new_window_even_after_record_disappears(tmp_path, damage):
    coordinator, calls, rows, clock, options, args = await review_fixture(tmp_path)
    await commit_review(coordinator, args)
    directory = options['db_path'].parent / 'provision-reviews'
    audit = next(directory.iterdir())
    if damage == 'content':
        value = json.loads(audit.read_bytes()); value['reviewId'] = 'f' * 32; audit.write_bytes(canonical(value))
    elif damage == 'truncate': audit.write_bytes(b'{')
    elif damage == 'permissions': audit.chmod(0o666)
    elif damage == 'symlink':
        moved = tmp_path / 'moved-audit'; audit.rename(moved); audit.symlink_to(moved)
    elif damage == 'hardlink': os.link(audit, tmp_path / 'extra-link')
    else: audit.unlink()
    with pytest.raises(LocalProvisioningError):
        await coordinator.prepare('must_not_reopen', confirmation=OPEN)
    if audit.exists() or audit.is_symlink(): audit.unlink()
    with pytest.raises(LocalProvisioningError):
        await coordinator.prepare('still_blocked', confirmation=OPEN)
    assert calls.count('apply') == 1
    await coordinator.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['cardId','userId','bindingEpoch'])
async def test_review_pins_refuse_another_card_owner_or_binding_epoch_after_restart(tmp_path, field):
    coordinator, calls, rows, clock, options, args = await review_fixture(tmp_path)
    await commit_review(coordinator, args)
    await coordinator.aclose()
    changed = dict(REVIEW_IDENTITY)
    changed[field] = {'cardId':'c'*32,'userId':'user_other','bindingEpoch':'2026-11-03T00:00:00Z'}[field]
    with pytest.raises(LocalProvisioningError):
        LocalProvisioningCoordinator(**{**options, 'review_identity': changed})


@pytest.mark.asyncio
async def test_active_context_or_runner_and_missing_unknown_receipt_never_gain_review_eligibility(tmp_path):
    coordinator, calls, rows, clock, options, args = await review_fixture(tmp_path)
    coordinator._contexts['synthetic-active'] = {}
    assert (await coordinator.archive_status('outer_review'))['eligible'] is False
    with pytest.raises(LocalProvisioningError):
        await coordinator.prepare_archive('outer_review', **args)
    coordinator._contexts.clear()
    waiter = asyncio.create_task(asyncio.Event().wait())
    coordinator._runner = waiter
    assert (await coordinator.archive_status('outer_review'))['eligible'] is False
    waiter.cancel(); await asyncio.gather(waiter, return_exceptions=True)
    assert (await coordinator.archive_status('outer_review'))['eligible'] is True
    coordinator._update('outer_review', 'unknown')
    with pytest.raises(LocalProvisioningError):
        await coordinator.archive_status('outer_review')
    await coordinator.aclose()


@pytest.mark.asyncio
async def test_review_append_failure_does_not_mutate_original_unknown_and_latches_new_openings(tmp_path, monkeypatch):
    coordinator, calls, rows, clock, options, args = await review_fixture(tmp_path)
    before = coordinator._row('outer_review')
    prepared = await coordinator.prepare_archive('outer_review', **args)
    def broken_write(*args, **kwargs):
        raise OSError('PRIVATE_DISK_FAILURE')
    monkeypatch.setattr('ai_cat_controller.local_gateway.provision_reviews.os.write', broken_write)
    with pytest.raises(LocalProvisioningError):
        await coordinator.confirm_archive('outer_review', review_token=prepared['reviewToken'], guard=lambda: True, **args)
    assert coordinator._row('outer_review') == before
    with pytest.raises(LocalProvisioningError):
        await coordinator.prepare('must_remain_blocked', confirmation=OPEN)
    await coordinator.aclose()


@pytest.mark.asyncio
async def test_review_guard_cannot_archive_a_row_changed_concurrently_during_readonly_check(tmp_path):
    coordinator, calls, rows, clock, options, args = await review_fixture(tmp_path)
    prepared = await coordinator.prepare_archive('outer_review', **args)
    before = coordinator._row('outer_review')
    async def guard():
        with sqlite3.connect(options['db_path']) as database:
            database.execute('UPDATE lp_requests SET updated=updated+1 WHERE request_id=?', ('outer_review',))
        return True
    with pytest.raises(LocalProvisioningError):
        await coordinator.confirm_archive('outer_review', review_token=prepared['reviewToken'], guard=guard, **args)
    assert not (options['db_path'].parent / 'provision-reviews').exists()
    after = coordinator._row('outer_review')
    assert after == {**before, 'updated': before['updated'] + 1}
    assert calls.count('apply') == 1
    await coordinator.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('result', ['connected','failed','not_sent'])
async def test_review_never_archives_a_known_terminal_result(tmp_path, result):
    coordinator, calls, rows, clock, options, args = await review_fixture(tmp_path, result=result)
    with pytest.raises(LocalProvisioningError):
        await coordinator.archive_status('outer_review')
    with pytest.raises(LocalProvisioningError):
        await coordinator.prepare_archive('outer_review', **args)
    assert not (options['db_path'].parent / 'provision-reviews').exists()
    await coordinator.aclose()


@pytest.mark.asyncio
async def test_review_maximum_is_256_without_pruning_and_a_full_store_keeps_next_unknown(tmp_path, monkeypatch):
    from ai_cat_controller.local_gateway import provision_reviews
    assert provision_reviews.MAX_REVIEWS == 256
    coordinator, calls, rows, clock, options, args = await review_fixture(tmp_path)
    await commit_review(coordinator, args)
    directory = options['db_path'].parent / 'provision-reviews'
    original_audits = {path.name: path.read_bytes() for path in directory.iterdir()}
    rows['prepared'] = await coordinator.prepare('outer_second', confirmation=OPEN)
    terminal = await coordinator.start('outer_second')
    clock.now = rows['prepared']['expiresAt'] + 90
    second_args = {'window_id': terminal['windowId'], 'network_request_id': terminal['networkRequestId'], 'acks': list(ACKNOWLEDGEMENTS)}
    second = await coordinator.prepare_archive('outer_second', **second_args)
    monkeypatch.setattr(provision_reviews, 'MAX_REVIEWS', 1)
    with pytest.raises(LocalProvisioningError):
        await coordinator.confirm_archive('outer_second', review_token=second['reviewToken'], guard=lambda: True, **second_args)
    assert coordinator._row('outer_second')['status'] == 'unknown'
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == original_audits
    await coordinator.aclose()


@pytest.mark.asyncio
async def test_prepare_is_private_durable_and_has_no_network_effect(tmp_path):
    calls, rows, _, _, options = fixture(tmp_path)
    coordinator = LocalProvisioningCoordinator(**options)
    prepared = rows['prepared'] = await coordinator.prepare('outer_one', confirmation=OPEN)
    assert prepared['requestId'] == 'outer_one' and prepared['status'] == 'prepared'
    invite = Invitation.parse(prepared['invitation'])
    assert (invite.device_id, invite.session_id) == (DEVICE, prepared['windowId'])
    assert prepared['expiresAt'] == 1180.0 and prepared['apName'].startswith('AI-CAT-')
    assert calls == []
    assert options['db_path'].stat().st_mode & 0o777 == 0o600
    assert options['db_path'].parent.stat().st_mode & 0o777 == 0o700
    await coordinator.aclose()


@pytest.mark.asyncio
async def test_prepare_replay_and_duplicate_start_never_create_second_window_or_ap(tmp_path):
    calls, rows, _, events, options = fixture(tmp_path, gated_apply=True)
    coordinator = LocalProvisioningCoordinator(**options)
    prepared = rows['prepared'] = await coordinator.prepare('outer_one', confirmation=OPEN)
    assert await coordinator.prepare('outer_one', confirmation=OPEN) == prepared
    first = asyncio.create_task(coordinator.start('outer_one'))
    await events[2].wait()
    second = asyncio.create_task(coordinator.start('outer_one'))
    events[3].set()
    assert (await first)['status'] == (await second)['status'] == 'connected'
    assert calls.count('begin') == calls.count('apply') == 1
    assert (await coordinator.prepare('outer_one', confirmation=OPEN))['status'] == 'connected'
    assert (await coordinator.start('outer_one'))['status'] == 'connected'
    assert calls.count('begin') == 1
    await coordinator.aclose()


@pytest.mark.parametrize('result', ['connected', 'failed', 'unknown', 'not_sent'])
@pytest.mark.asyncio
async def test_finish_persists_original_authenticated_receipt_across_restart(tmp_path, result):
    calls, rows, _, _, options = fixture(tmp_path, result=result)
    coordinator = LocalProvisioningCoordinator(**options)
    prepared = rows['prepared'] = await coordinator.prepare('outer_one', confirmation=OPEN)
    terminal = await coordinator.start('outer_one')
    assert terminal['status'] == result
    assert terminal['networkRequestId'] == 'wifi_synthetic'
    assert plain_receipt(prepared, terminal) == {'status': result}
    assert calls == ['begin', 'server-start', 'apply', 'server-close', 'backend-close']
    first = await coordinator.get_receipt('outer_one')
    for _ in range(80):
        assert await coordinator.get_receipt('outer_one') == first
    await coordinator.aclose()
    reopened = LocalProvisioningCoordinator(**options)
    assert await reopened.get_receipt('outer_one') == first
    assert plain_receipt(prepared, first) == {'status': result}
    await reopened.aclose()


@pytest.mark.asyncio
async def test_unknown_is_retained_and_blocks_new_windows(tmp_path):
    _, rows, _, _, options = fixture(tmp_path, result='unknown')
    coordinator = LocalProvisioningCoordinator(**options)
    rows['prepared'] = await coordinator.prepare('outer_one', confirmation=OPEN)
    await coordinator.start('outer_one')
    with pytest.raises(LocalProvisioningError):
        await coordinator.prepare('outer_two', confirmation=OPEN)
    await coordinator.aclose()
    reopened = LocalProvisioningCoordinator(**options)
    with pytest.raises(LocalProvisioningError):
        await reopened.prepare('outer_two', confirmation=OPEN)
    assert (await reopened.status('outer_one'))['status'] == 'unknown'
    await reopened.aclose()


@pytest.mark.asyncio
async def test_prepared_or_open_window_blocks_other_request_id(tmp_path):
    _, rows, _, events, options = fixture(tmp_path, configure=False)
    coordinator = LocalProvisioningCoordinator(**options)
    rows['prepared'] = await coordinator.prepare('outer_one', confirmation=OPEN)
    with pytest.raises(LocalProvisioningError):
        await coordinator.prepare('outer_two', confirmation=OPEN)
    task = asyncio.create_task(coordinator.start('outer_one'))
    await events[1].wait()
    with pytest.raises(LocalProvisioningError):
        await coordinator.prepare('outer_two', confirmation=OPEN)
    await coordinator.aclose()
    assert (await task)['status'] == 'unknown'


@pytest.mark.asyncio
async def test_prepared_expiry_and_restart_are_not_sent_without_opening_ap(tmp_path):
    calls, rows, clock, _, options = fixture(tmp_path)
    coordinator = LocalProvisioningCoordinator(**options)
    rows['prepared'] = await coordinator.prepare('outer_one', confirmation=OPEN)
    clock.now += 181
    assert (await coordinator.status('outer_one'))['status'] == 'not_sent'
    assert (await coordinator.start('outer_one'))['status'] == 'not_sent'
    assert calls == []
    await coordinator.prepare('outer_two', confirmation=OPEN)
    await coordinator.aclose()
    reopened = LocalProvisioningCoordinator(**options)
    assert (await reopened.status('outer_two'))['status'] == 'not_sent'
    assert calls == []
    await reopened.aclose()


@pytest.mark.parametrize('state', ['opening', 'open', 'accepted', 'connecting'])
@pytest.mark.asyncio
async def test_crash_recovery_of_any_network_capable_state_is_unknown_never_replayed(tmp_path, state):
    calls, _, _, _, options = fixture(tmp_path)
    coordinator = LocalProvisioningCoordinator(**options)
    await coordinator.prepare('outer_one', confirmation=OPEN)
    await coordinator.aclose()
    with sqlite3.connect(options['db_path']) as c:
        c.execute('UPDATE lp_requests SET status=?,network_request_id=?', (state, 'wifi_synthetic' if state in {'accepted', 'connecting'} else None))
    reopened = LocalProvisioningCoordinator(**options)
    assert (await reopened.status('outer_one'))['status'] == 'unknown'
    assert (await reopened.start('outer_one'))['status'] == 'unknown'
    assert calls == []
    with pytest.raises(LocalProvisioningError):
        await reopened.prepare('outer_two', confirmation=OPEN)
    await reopened.aclose()


@pytest.mark.asyncio
async def test_no_target_apply_until_acceptance_is_drained_and_start_pending_called(tmp_path):
    calls, rows, _, events, options = fixture(tmp_path, drained=False)
    coordinator = LocalProvisioningCoordinator(**options)
    rows['prepared'] = await coordinator.prepare('outer_one', confirmation=OPEN)
    task = asyncio.create_task(coordinator.start('outer_one'))
    await events[1].wait()
    await asyncio.sleep(0)
    assert 'apply' not in calls
    assert (await coordinator.status('outer_one'))['status'] == 'accepted'
    rows['window'].start_pending('wifi_synthetic')
    assert (await task)['status'] == 'connected'
    assert calls.count('apply') == 1
    await coordinator.aclose()


@pytest.mark.asyncio
async def test_shutdown_during_target_apply_waits_then_preserves_actual_final_result(tmp_path):
    calls, rows, _, events, options = fixture(tmp_path, gated_apply=True)
    coordinator = LocalProvisioningCoordinator(**options)
    prepared = rows['prepared'] = await coordinator.prepare('outer_one', confirmation=OPEN)
    task = asyncio.create_task(coordinator.start('outer_one'))
    await events[2].wait()
    closing = asyncio.create_task(coordinator.aclose())
    await asyncio.sleep(0)
    assert not closing.done()
    events[3].set()
    await closing
    terminal = await task
    assert terminal['status'] == 'connected'
    assert plain_receipt(prepared, terminal) == {'status': 'connected'}
    assert calls.count('apply') == 1


@pytest.mark.asyncio
async def test_no_target_credentials_or_raw_backend_errors_enter_ledger_or_error_text(tmp_path):
    _, rows, _, _, options = fixture(tmp_path)
    coordinator = LocalProvisioningCoordinator(**options)
    rows['prepared'] = await coordinator.prepare('outer_one', confirmation=OPEN)
    await coordinator.start('outer_one')
    await coordinator.aclose()
    raw = options['db_path'].read_bytes()
    assert b'synthetic-wifi-password' not in raw and '合成网络'.encode() not in raw
    _, rows, _, _, options2 = fixture(tmp_path / 'other', begin_error=True)
    coordinator = LocalProvisioningCoordinator(**options2)
    rows['prepared'] = await coordinator.prepare('outer_one', confirmation=OPEN)
    assert (await coordinator.start('outer_one'))['status'] == 'unknown'
    assert b'synthetic secret' not in options2['db_path'].read_bytes()
    await coordinator.aclose()


@pytest.mark.parametrize('request_id', ['', None, ['one'], 'x' * 65, 'with/slash', 'with space'])
@pytest.mark.asyncio
async def test_invalid_outer_id_cannot_prepare_or_touch_network(tmp_path, request_id):
    calls, _, _, _, options = fixture(tmp_path)
    coordinator = LocalProvisioningCoordinator(**options)
    with pytest.raises(LocalProvisioningError):
        await coordinator.prepare(request_id, confirmation=OPEN)
    assert calls == []
    await coordinator.aclose()


@pytest.mark.parametrize('confirmation', [None, True, '', 'open', ['open-local-provisioning']])
@pytest.mark.asyncio
async def test_exact_explicit_confirmation_required(tmp_path, confirmation):
    calls, _, _, _, options = fixture(tmp_path)
    coordinator = LocalProvisioningCoordinator(**options)
    with pytest.raises(LocalProvisioningError):
        await coordinator.prepare('outer_one', confirmation=confirmation)
    assert calls == []
    await coordinator.aclose()


@pytest.mark.asyncio
async def test_missing_request_read_does_not_create_any_window(tmp_path):
    calls, _, _, _, options = fixture(tmp_path)
    coordinator = LocalProvisioningCoordinator(**options)
    for method in (coordinator.status, coordinator.get_receipt, coordinator.start):
        with pytest.raises(LocalProvisioningError):
            await method('outer_absent')
    assert calls == []
    await coordinator.aclose()


def test_ledger_is_exclusive_device_pinned_and_rejects_foreign_schema(tmp_path):
    _, _, _, _, options = fixture(tmp_path)
    one = LocalProvisioningCoordinator(**options)
    with pytest.raises(LocalProvisioningError):
        LocalProvisioningCoordinator(**options)
    one.close()
    with pytest.raises(LocalProvisioningError):
        LocalProvisioningCoordinator(**{**options, 'device_id': 'dev_' + 'b' * 32})
    foreign = tmp_path / 'foreign'
    foreign.mkdir(mode=0o700)
    path = foreign / 'store.sqlite3'
    with sqlite3.connect(path) as c:
        c.execute('CREATE TABLE other(value TEXT)')
    path.chmod(0o600)
    with pytest.raises(LocalProvisioningError):
        LocalProvisioningCoordinator(**{**options, 'db_path': path})
    with sqlite3.connect(path) as c:
        assert [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")] == ['other']


def test_symlink_public_or_hardlinked_ledger_is_rejected(tmp_path):
    _, _, _, _, options = fixture(tmp_path)
    one = LocalProvisioningCoordinator(**options); one.close()
    path = options['db_path']
    path.chmod(0o644)
    with pytest.raises(LocalProvisioningError):
        LocalProvisioningCoordinator(**options)
    path.chmod(0o600)
    os.link(path, path.with_name('hardlink.sqlite3'))
    with pytest.raises(LocalProvisioningError):
        LocalProvisioningCoordinator(**options)
    linked = tmp_path / 'linked'
    linked.symlink_to(path.parent, target_is_directory=True)
    with pytest.raises(LocalProvisioningError):
        LocalProvisioningCoordinator(**{**options, 'db_path': linked / 'store.sqlite3'})


@pytest.mark.asyncio
async def test_status_and_receipt_never_disclose_private_invitation(tmp_path):
    _, rows, _, _, options = fixture(tmp_path)
    coordinator = LocalProvisioningCoordinator(**options)
    prepared = rows['prepared'] = await coordinator.prepare('outer_one', confirmation=OPEN)
    status = await coordinator.status('outer_one')
    assert 'invitation' not in status and Invitation.parse(prepared['invitation']).key_hex not in json.dumps(status)
    await coordinator.start('outer_one')
    assert 'invitation' not in await coordinator.get_receipt('outer_one')
    await coordinator.aclose()


@pytest.mark.asyncio
async def test_wall_clock_rollback_does_not_extend_prepared_monotonic_deadline(tmp_path):
    calls, _, clock, _, options = fixture(tmp_path)
    monotonic = Clock()
    coordinator = LocalProvisioningCoordinator(**{**options, 'monotonic': monotonic})
    await coordinator.prepare('outer_one', confirmation=OPEN)
    monotonic.now += 181
    clock.now -= 10
    assert (await coordinator.start('outer_one'))['status'] == 'not_sent'
    assert calls == []
    await coordinator.aclose()


@pytest.mark.parametrize('drained', [False, True])
@pytest.mark.asyncio
async def test_expired_open_window_with_no_target_dispatch_has_not_sent_receipt_only_when_known(tmp_path, drained):
    calls, rows, clock, _, options = fixture(tmp_path, configure=not drained, drained=False)
    original = options['server_factory']
    class Server(original):
        async def start(self):
            await super().start()
            clock.now += 181
    coordinator = LocalProvisioningCoordinator(**{**options, 'server_factory': Server})
    prepared = rows['prepared'] = await coordinator.prepare('outer_one', confirmation=OPEN)
    result = await coordinator.start('outer_one')
    assert result['status'] == 'not_sent' and 'apply' not in calls
    if result['networkRequestId']:
        assert plain_receipt(prepared, result) == {'status': 'not_sent'}
    else:
        assert result['receiptQr'] is None
    await coordinator.aclose()


@pytest.mark.asyncio
async def test_client_cancellation_cannot_cancel_or_replay_a_started_network_task(tmp_path):
    calls, rows, _, events, options = fixture(tmp_path, gated_apply=True)
    coordinator = LocalProvisioningCoordinator(**options)
    rows['prepared'] = await coordinator.prepare('outer_one', confirmation=OPEN)
    caller = asyncio.create_task(coordinator.start('outer_one'))
    await events[2].wait()
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert calls.count('apply') == 1
    events[3].set()
    assert (await coordinator.start('outer_one'))['status'] == 'connected'
    assert calls.count('begin') == calls.count('apply') == 1
    await coordinator.aclose()


@pytest.mark.asyncio
async def test_shutdown_during_ap_opening_never_claims_not_sent_or_starts_tcp(tmp_path):
    calls, rows, _, events, options = fixture(tmp_path)
    original = options['backend_factory']
    class Backend(original):
        async def begin(self, name, password, *, timeout):
            await super().begin(name, password, timeout=timeout)
            await events[3].wait()
    coordinator = LocalProvisioningCoordinator(**{**options, 'backend_factory': Backend})
    rows['prepared'] = await coordinator.prepare('outer_one', confirmation=OPEN)
    task = asyncio.create_task(coordinator.start('outer_one'))
    await events[0].wait()
    closing = asyncio.create_task(coordinator.aclose())
    await asyncio.sleep(0)
    events[3].set()
    await closing
    assert (await task)['status'] == 'unknown'
    assert 'server-start' not in calls and 'apply' not in calls


@pytest.mark.asyncio
async def test_cleanup_failure_retains_real_connected_receipt_but_blocks_new_network_windows(tmp_path):
    _, rows, _, _, options = fixture(tmp_path, close_error=True)
    coordinator = LocalProvisioningCoordinator(**options)
    prepared = rows['prepared'] = await coordinator.prepare('outer_one', confirmation=OPEN)
    result = await coordinator.start('outer_one')
    assert result['status'] == 'connected'
    assert plain_receipt(prepared, await coordinator.get_receipt('outer_one')) == {'status': 'connected'}
    with pytest.raises(LocalProvisioningError):
        await coordinator.prepare('outer_two', confirmation=OPEN)
    await coordinator.aclose()


@pytest.mark.asyncio
async def test_persistence_failure_before_opening_latches_closed_without_any_network_effect(tmp_path):
    calls, _, _, _, options = fixture(tmp_path)
    coordinator = LocalProvisioningCoordinator(**options)
    await coordinator.prepare('outer_one', confirmation=OPEN)
    with sqlite3.connect(options['db_path']) as c:
        c.execute("CREATE TRIGGER synthetic_failure BEFORE UPDATE ON lp_requests WHEN NEW.status='opening' BEGIN SELECT RAISE(ABORT,'private synthetic error'); END")
    with pytest.raises(LocalProvisioningError) as caught:
        await coordinator.start('outer_one')
    assert 'private synthetic' not in str(caught.value) and calls == []
    with pytest.raises(LocalProvisioningError):
        await coordinator.status('outer_one')
    await coordinator.aclose()


@pytest.mark.asyncio
async def test_ledger_capacity_is_bounded_and_does_not_delete_history(tmp_path, monkeypatch):
    from ai_cat_controller.local_gateway import provisioning
    monkeypatch.setattr(provisioning, 'MAX_WINDOWS', 2)
    calls, _, clock, _, options = fixture(tmp_path)
    coordinator = LocalProvisioningCoordinator(**options)
    for request_id in ('outer_one', 'outer_two'):
        await coordinator.prepare(request_id, confirmation=OPEN)
        clock.now += 181
        assert (await coordinator.status(request_id))['status'] == 'not_sent'
    with pytest.raises(LocalProvisioningError):
        await coordinator.prepare('outer_three', confirmation=OPEN)
    assert calls == []
    assert (await coordinator.status('outer_one'))['status'] == 'not_sent'
    await coordinator.aclose()


@pytest.mark.asyncio
async def test_private_resume_reads_original_invitation_without_new_deadline_or_ap(tmp_path):
    calls, _, clock, _, options = fixture(tmp_path)
    coordinator = LocalProvisioningCoordinator(**options)
    prepared = await coordinator.prepare('outer_one', confirmation=OPEN)
    assert await coordinator.resume('outer_one') == prepared
    clock.now += 181
    resumed = await coordinator.resume('outer_one')
    assert resumed['status'] == 'not_sent'
    assert resumed['invitation'] == prepared['invitation']
    assert resumed['windowId'] == prepared['windowId'] and resumed['expiresAt'] == prepared['expiresAt']
    assert (await coordinator.start('outer_one'))['status'] == 'not_sent'
    assert calls == []
    await coordinator.aclose()


@pytest.mark.asyncio
async def test_unknown_resume_survives_restart_for_original_receipt_and_never_unlocks_new_window(tmp_path):
    calls, rows, _, _, options = fixture(tmp_path, result='unknown')
    coordinator = LocalProvisioningCoordinator(**options)
    prepared = rows['prepared'] = await coordinator.prepare('outer_one', confirmation=OPEN)
    terminal = await coordinator.start('outer_one')
    await coordinator.aclose()
    reopened = LocalProvisioningCoordinator(**options)
    resumed = await reopened.resume('outer_one')
    assert resumed['invitation'] == prepared['invitation']
    assert resumed['receiptQr'] == terminal['receiptQr']
    assert plain_receipt(resumed, resumed) == {'status': 'unknown'}
    with pytest.raises(LocalProvisioningError):
        await reopened.prepare('outer_two', confirmation=OPEN)
    assert calls.count('begin') == calls.count('apply') == 1
    await reopened.aclose()


@pytest.mark.asyncio
async def test_current_window_is_metadata_only_and_empty_lookup_has_no_effect(tmp_path):
    calls, _, _, _, options = fixture(tmp_path)
    coordinator = LocalProvisioningCoordinator(**options)
    assert await coordinator.current_window() == {'window': None}
    prepared = await coordinator.prepare('outer_one', confirmation=OPEN)
    result = await coordinator.current_window()
    assert result == {'window': {key: value for key, value in prepared.items() if key != 'invitation'}}
    assert 'invitation' not in json.dumps(result)
    assert Invitation.parse(prepared['invitation']).key_hex not in json.dumps(result)
    assert calls == []
    await coordinator.aclose()


@pytest.mark.asyncio
async def test_current_window_uses_insertion_order_not_wall_clock_sorting(tmp_path):
    calls, _, clock, _, options = fixture(tmp_path)
    coordinator = LocalProvisioningCoordinator(**options)
    await coordinator.prepare('outer_one', confirmation=OPEN)
    clock.now += 181
    await coordinator.status('outer_one')
    clock.now -= 300
    newest = await coordinator.prepare('outer_two', confirmation=OPEN)
    assert (await coordinator.current_window())['window']['requestId'] == newest['requestId']
    assert calls == []
    await coordinator.aclose()


@pytest.mark.asyncio
async def test_resume_missing_id_never_creates_record_or_renews_latest_window(tmp_path):
    calls, _, _, _, options = fixture(tmp_path)
    coordinator = LocalProvisioningCoordinator(**options)
    prepared = await coordinator.prepare('outer_one', confirmation=OPEN)
    for bad in ('outer_absent', '', None):
        with pytest.raises(LocalProvisioningError):
            await coordinator.resume(bad)
    assert (await coordinator.current_window())['window']['requestId'] == prepared['requestId']
    assert calls == []
    await coordinator.aclose()


@pytest.mark.parametrize('answer', [False, None, 1, 'true'])
@pytest.mark.asyncio
async def test_boot_offline_guard_decline_is_not_sent_without_backend_begin(tmp_path, answer):
    calls, _, _, _, options = fixture(tmp_path)
    coordinator = LocalProvisioningCoordinator(**options)
    await coordinator.prepare('outer_boot', confirmation=OPEN)
    checked = []
    async def guard():
        checked.append('read-only-offline-check')
        return answer
    result = await coordinator.start('outer_boot', offline_guard=guard)
    assert result['status'] == 'not_sent' and result['networkRequestId'] is None
    assert checked == ['read-only-offline-check'] and calls == []
    await coordinator.aclose()


@pytest.mark.asyncio
async def test_boot_offline_guard_exception_is_safe_not_sent_without_ap(tmp_path):
    calls, _, _, _, options = fixture(tmp_path)
    coordinator = LocalProvisioningCoordinator(**options)
    await coordinator.prepare('outer_boot', confirmation=OPEN)
    async def guard():
        raise RuntimeError('synthetic private NM error')
    result = await coordinator.start('outer_boot', offline_guard=guard)
    assert result['status'] == 'not_sent' and calls == []
    assert 'synthetic private' not in json.dumps(result)
    await coordinator.aclose()


@pytest.mark.asyncio
async def test_boot_offline_guard_is_checked_immediately_before_persisted_opening_and_begin(tmp_path):
    calls, rows, _, _, options = fixture(tmp_path)
    original = options['backend_factory']
    class Backend(original):
        async def begin(self, name, password, *, timeout):
            with sqlite3.connect(options['db_path']) as c:
                assert c.execute('SELECT status FROM lp_requests').fetchone() == ('opening',)
            assert calls == ['offline-guard']
            await super().begin(name, password, timeout=timeout)
    coordinator = LocalProvisioningCoordinator(**{**options, 'backend_factory': Backend})
    rows['prepared'] = await coordinator.prepare('outer_boot', confirmation=OPEN)
    async def guard():
        with sqlite3.connect(options['db_path']) as c:
            assert c.execute('SELECT status FROM lp_requests').fetchone() == ('prepared',)
        calls.append('offline-guard')
        return True
    assert (await coordinator.start('outer_boot', offline_guard=guard))['status'] == 'connected'
    assert calls.count('begin') == 1
    await coordinator.aclose()


@pytest.mark.asyncio
async def test_boot_guard_pending_does_not_claim_opening_and_recheck_connected_prevents_ap(tmp_path):
    calls, _, _, _, options = fixture(tmp_path)
    coordinator = LocalProvisioningCoordinator(**options)
    await coordinator.prepare('outer_boot', confirmation=OPEN)
    entered, finish = asyncio.Event(), asyncio.Event()
    state = {'disconnected': True}
    async def guard():
        entered.set()
        await finish.wait()
        return state['disconnected']
    task = asyncio.create_task(coordinator.start('outer_boot', offline_guard=guard))
    await entered.wait()
    assert (await coordinator.status('outer_boot'))['status'] == 'prepared'
    state['disconnected'] = False
    finish.set()
    assert (await task)['status'] == 'not_sent' and calls == []
    await coordinator.aclose()


@pytest.mark.asyncio
async def test_boot_guard_cannot_extend_expired_prepared_window(tmp_path):
    calls, _, clock, _, options = fixture(tmp_path)
    coordinator = LocalProvisioningCoordinator(**options)
    await coordinator.prepare('outer_boot', confirmation=OPEN)
    async def guard():
        clock.now += 181
        return True
    assert (await coordinator.start('outer_boot', offline_guard=guard))['status'] == 'not_sent'
    assert calls == []
    await coordinator.aclose()
