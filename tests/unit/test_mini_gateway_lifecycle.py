"""Lifecycle gateway foundation: temporary ledgers and an explicit fake executor only."""

import asyncio
import hashlib
import sqlite3

import pytest

from ai_cat_controller.mini_gateway.lifecycle import (
    LifecycleError, LifecycleOutcome, LifecycleProof, LocalLifecycleService,
)
from ai_cat_controller.mini_gateway.lifecycle_models import Command, SCOPES


SERIAL, WEB_PET, OWNER = 'SYNTHETIC-K1-123', 'pet_web', 'a' * 64
USER, PET = 'usr_mini', 'pet_mini'


async def allow():
    return None


async def deny():
    raise PermissionError('private-denial')


class Clock:
    def __init__(self):
        self.value = 1000.0

    def __call__(self):
        return self.value


class Executor:
    """Not a production verifier: every successful proof here is synthetic."""
    def __init__(self, clock):
        self.clock, self.calls, self.probes = clock, [], []
        self.revision, self.release = 'b' * 64, 'c' * 64
        self.owner, self.serial = OWNER, hashlib.sha256(SERIAL.encode()).hexdigest()
        self.status, self.hook, self.probe_hook = 'completed', None, None

    async def probe(self, *, nonce, user_id, pet_id, kind, scope):
        self.probes.append(nonce)
        if self.probe_hook:
            await self.probe_hook()
        return LifecycleProof(nonce=nonce, kind=kind, scope=scope,
            serial_sha256=self.serial, owner_sha256=self.owner,
            release_sha256=self.release, revision_sha256=self.revision,
            expires_at=self.clock() + 2, evidence=object())

    async def execute(self, *, proof, user_id, pet_id, request_id, kind, scope):
        self.calls.append((request_id, kind, scope, proof.nonce))
        if self.hook:
            await self.hook()
        return LifecycleOutcome(request_id=request_id, kind=kind, scope=scope, status=self.status)


def service(tmp_path, *, clock=None, executor=True, **overrides):
    clock = clock or Clock()
    device = Executor(clock) if executor is True else executor
    config = dict(db_path=tmp_path / 'private' / 'lifecycle.sqlite3',
        expected_serial=SERIAL, product_pet_id=WEB_PET, expected_owner_sha256=OWNER,
        enabled_kinds=tuple(SCOPES), executor=device, operator_check=lambda **_: True,
        retire_authorization=lambda **_: None, clock=clock)
    config.update(overrides)
    return LocalLifecycleService(**config), device, clock


async def challenge(client, kind='memory_clear'):
    return await client.challenge(user_id=USER, pet_id=PET, kind=kind, scope=SCOPES[kind],
                                  authenticate=allow, authorize=allow)


def command(value, request='one'):
    return {'requestId': request, 'kind': value['kind'], 'scope': value['scope'],
            'challengeId': value['challengeId'], 'confirmVersion': 1}


async def submit(client, value, **overrides):
    args = dict(user_id=USER, pet_id=PET, command=value, authenticate=allow, authorize=allow)
    args.update(overrides)
    return await client.submit(**args)


@pytest.mark.asyncio
async def test_default_disabled_and_missing_real_executor_never_report_ready(tmp_path):
    client, _, _ = service(tmp_path, executor=None)
    result = await client.snapshot(user_id=USER, pet_id=PET, authenticate=allow, authorize=allow)
    assert result['verified'] is False and all(not x['available'] for x in result['operations'])
    with pytest.raises(LifecycleError):
        await challenge(client)
    client.close()


@pytest.mark.asyncio
async def test_explicit_flag_is_not_execution_proof_or_operator_authority(tmp_path):
    client, executor, _ = service(tmp_path, enabled_kinds=(), operator_check=None)
    result = await client.snapshot(user_id=USER, pet_id=PET, authenticate=allow, authorize=allow)
    assert all(not x['available'] for x in result['operations']) and not executor.calls
    with pytest.raises(LifecycleError):
        await challenge(client)
    client.close()


@pytest.mark.parametrize('kind', SCOPES)
@pytest.mark.asyncio
async def test_each_kind_needs_challenge_then_fresh_proof_and_persistent_single_dispatch(tmp_path, kind):
    client, executor, _ = service(tmp_path)
    prepared = await challenge(client, kind)
    assert len(executor.probes) == 1 and not executor.calls
    body = command(prepared)
    result = await submit(client, body)
    assert result['status'] == 'completed' and result['scope'] == SCOPES[kind]
    assert len(executor.probes) == 2 and len(set(executor.probes)) == 2
    assert len(executor.calls) == 1
    assert await submit(client, body, authorize=deny) == result
    assert len(executor.calls) == 1
    client.close()


@pytest.mark.asyncio
async def test_scope_confirmation_and_unknown_fields_are_strict(tmp_path):
    client, executor, _ = service(tmp_path)
    prepared = await challenge(client)
    original = command(prepared)
    for field, value in [('scope', SCOPES['product_reset']), ('confirmVersion', True),
                         ('confirmVersion', 2), ('metadata', {'admin': True}),
                         ('kind', 'delete_files'), ('challengeId', 'short')]:
        with pytest.raises(ValueError):
            Command.model_validate({**original, field: value})
    assert not executor.calls
    client.close()


@pytest.mark.asyncio
async def test_challenge_expires_and_cannot_cross_subject_or_device(tmp_path):
    client, executor, clock = service(tmp_path)
    prepared = await challenge(client)
    with pytest.raises(LifecycleError):
        await submit(client, command(prepared), user_id='another_user')
    with pytest.raises(LifecycleError):
        await submit(client, command(prepared), pet_id='another_pet')
    clock.value += 61
    with pytest.raises(LifecycleError):
        await submit(client, command(prepared))
    assert not executor.calls
    client.close()


@pytest.mark.parametrize('attribute', ['revision', 'release', 'owner', 'serial'])
@pytest.mark.asyncio
async def test_changed_device_owner_release_or_revision_revokes_confirmation(tmp_path, attribute):
    client, executor, _ = service(tmp_path)
    prepared = await challenge(client)
    setattr(executor, attribute, 'd' * 64)
    with pytest.raises(LifecycleError):
        await submit(client, command(prepared))
    assert not executor.calls
    client.close()


@pytest.mark.asyncio
async def test_auth_rechecked_before_dispatch_and_raw_failure_not_exposed(tmp_path):
    client, executor, _ = service(tmp_path)
    prepared = await challenge(client)
    called = 0
    async def revoke():
        nonlocal called
        called += 1
        if called > 1:
            raise PermissionError('private-denial')
    with pytest.raises(LifecycleError) as error:
        await submit(client, command(prepared), authorize=revoke)
    assert 'private-denial' not in str(error.value) and not executor.calls
    client.close()


@pytest.mark.parametrize('failure', ['owner', 'revision'])
@pytest.mark.asyncio
async def test_failed_preflight_consumes_confirmation_even_if_permissions_or_revision_later_restore(tmp_path, failure):
    client, executor, _ = service(tmp_path)
    body = command(await challenge(client))
    if failure == 'revision':
        executor.revision = 'd' * 64
    with pytest.raises(LifecycleError):
        await submit(client, body, authorize=deny if failure == 'owner' else allow)
    executor.revision = 'b' * 64
    with pytest.raises(LifecycleError):
        await submit(client, body)
    assert not executor.calls
    client.close()


@pytest.mark.asyncio
async def test_snapshot_does_not_keep_earlier_available_flag_after_late_auth_revocation(tmp_path):
    client, _, _ = service(tmp_path)
    called = 0
    async def revoke_later():
        nonlocal called
        called += 1
        if called >= 3:
            raise PermissionError('private-revocation')
    with pytest.raises(LifecycleError):
        await client.snapshot(user_id=USER, pet_id=PET, authenticate=revoke_later, authorize=allow)
    client.close()


@pytest.mark.asyncio
async def test_plain_json_ready_claim_is_not_a_private_live_proof(tmp_path):
    client, executor, _ = service(tmp_path)
    async def fake_ready(**_):
        return {'verified': True, 'cohortReady': True, 'memoryClear': True}
    executor.probe = fake_ready
    result = await client.snapshot(user_id=USER, pet_id=PET, authenticate=allow, authorize=allow)
    assert not result['verified'] and not any(item['available'] for item in result['operations'])
    with pytest.raises(LifecycleError):
        await challenge(client)
    assert not executor.calls
    client.close()


@pytest.mark.asyncio
async def test_trusted_not_sent_outcome_allows_a_new_confirmation_not_an_old_replay(tmp_path):
    client, executor, _ = service(tmp_path)
    executor.status = 'not_sent'
    body = command(await challenge(client))
    result = await submit(client, body)
    assert result['status'] == 'not_sent'
    assert await submit(client, body) == result
    executor.status = 'completed'
    result = await submit(client, command(await challenge(client), request='next'))
    assert result['status'] == 'completed' and len(executor.calls) == 2
    client.close()


@pytest.mark.asyncio
async def test_restart_invalidates_unsubmitted_challenge_even_without_unknown_intents(tmp_path):
    client, _, _ = service(tmp_path)
    body = command(await challenge(client))
    client.close()
    restored, executor, _ = service(tmp_path)
    with pytest.raises(LifecycleError):
        await submit(restored, body)
    assert not executor.calls
    restored.close()


@pytest.mark.asyncio
async def test_reset_requires_separate_operator_and_unbind_requires_retirement_handler(tmp_path):
    client, executor, _ = service(tmp_path, operator_check=None, retire_authorization=None)
    with pytest.raises(LifecycleError):
        await challenge(client, 'product_reset')
    with pytest.raises(LifecycleError):
        await challenge(client, 'unbind')
    assert not executor.calls
    client.close()


@pytest.mark.asyncio
async def test_lost_operator_permission_after_challenge_prevents_reset(tmp_path):
    permitted = True
    client, executor, _ = service(tmp_path, operator_check=lambda **_: permitted)
    prepared = await challenge(client, 'product_reset')
    permitted = False
    with pytest.raises(LifecycleError):
        await submit(client, command(prepared))
    assert not executor.calls
    client.close()


@pytest.mark.asyncio
async def test_unknown_is_durable_blocks_all_new_danger_and_never_retries(tmp_path):
    client, executor, _ = service(tmp_path)
    executor.status = 'unknown'
    prepared = await challenge(client)
    body = command(prepared)
    result = await submit(client, body)
    assert result['status'] == 'unknown'
    assert await submit(client, body) == result and len(executor.calls) == 1
    with pytest.raises(LifecycleError):
        await challenge(client, 'unbind')
    client.close()
    restored, new_executor, _ = service(tmp_path)
    assert (await restored.receipt(user_id=USER, pet_id=PET, request_id='one', authenticate=allow)) == result
    with pytest.raises(LifecycleError):
        await challenge(restored)
    assert not new_executor.calls
    restored.close()


@pytest.mark.asyncio
async def test_bad_ack_or_exception_after_entering_executor_is_unknown(tmp_path):
    client, executor, _ = service(tmp_path)
    async def broken():
        raise OSError('private-key-and-message')
    executor.hook = broken
    result = await submit(client, command(await challenge(client)))
    assert result['status'] == 'unknown' and 'private' not in str(result)
    assert len(executor.calls) == 1
    client.close()


@pytest.mark.asyncio
async def test_receipts_survive_unbind_and_authentication_remains_required(tmp_path):
    retired = []
    client, executor, _ = service(tmp_path, retire_authorization=lambda **value: retired.append(value['kind']))
    result = await submit(client, command(await challenge(client, 'unbind')))
    assert retired == ['unbind'] and result['status'] == 'completed'
    assert await client.receipt(user_id=USER, pet_id=PET, request_id='one', authenticate=allow) == result
    with pytest.raises(LifecycleError):
        await client.receipt(user_id='other', pet_id=PET, request_id='one', authenticate=allow)
    with pytest.raises(LifecycleError):
        await client.receipt(user_id=USER, pet_id=PET, request_id='one', authenticate=deny)
    with pytest.raises(LifecycleError):
        await challenge(client)
    assert len(executor.calls) == 1
    client.close()


@pytest.mark.asyncio
async def test_retirement_failure_is_unknown_not_false_complete(tmp_path):
    def broken(**_):
        raise OSError('private-retirement-error')
    client, _, _ = service(tmp_path, retire_authorization=broken)
    result = await submit(client, command(await challenge(client, 'unbind')))
    assert result['status'] == 'unknown'
    client.close()


@pytest.mark.asyncio
async def test_challenge_single_use_request_conflict_and_no_plaintext_proof_in_ledger(tmp_path):
    client, executor, _ = service(tmp_path)
    prepared = await challenge(client)
    body = command(prepared)
    await submit(client, body)
    with pytest.raises(LifecycleError):
        await submit(client, {**body, 'requestId': 'two'})
    with pytest.raises(LifecycleError):
        await submit(client, {**body, 'challengeId': 'x' * 43})
    raw = client.path.read_bytes()
    assert prepared['challengeId'].encode() not in raw
    assert all(nonce.encode() not in raw for nonce in executor.probes)
    assert len(executor.calls) == 1
    client.close()


@pytest.mark.asyncio
async def test_caller_cancellation_does_not_abandon_started_intent(tmp_path):
    client, executor, _ = service(tmp_path)
    entered, finish = asyncio.Event(), asyncio.Event()
    async def block():
        entered.set()
        await finish.wait()
    executor.hook = block
    task = asyncio.create_task(submit(client, command(await challenge(client))))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(RuntimeError):
        client.close()
    finish.set()
    await client.aclose()
    restored, new_executor, _ = service(tmp_path)
    assert (await restored.receipt(user_id=USER, pet_id=PET, request_id='one', authenticate=allow))['status'] == 'completed'
    assert not new_executor.calls
    restored.close()


@pytest.mark.asyncio
async def test_restart_sending_becomes_unknown_and_restart_challenge_never_replays(tmp_path):
    client, executor, _ = service(tmp_path)
    prepared = await challenge(client)
    path = client.path
    client.close()
    with sqlite3.connect(path) as c:
        c.execute("INSERT INTO lc_requests VALUES(?,?,?,?,?,?,'sending',?)", (USER, PET, 'old', 'memory_clear', SCOPES['memory_clear'], 'e' * 64, '2026-10-28T00:00:00Z'))
    restored, _, _ = service(tmp_path)
    assert (await restored.receipt(user_id=USER, pet_id=PET, request_id='old', authenticate=allow))['status'] == 'unknown'
    with pytest.raises(LifecycleError):
        await submit(restored, command(prepared))
    assert not executor.calls
    restored.close()


def test_store_rejects_other_schema_rebinding_links_public_permissions_and_second_owner(tmp_path):
    client, _, _ = service(tmp_path)
    with pytest.raises(ValueError):
        service(tmp_path)
    client.close()
    with pytest.raises(ValueError):
        service(tmp_path, expected_serial='OTHER-SERIAL-123')
    client.path.chmod(0o644)
    with pytest.raises(ValueError):
        service(tmp_path)
