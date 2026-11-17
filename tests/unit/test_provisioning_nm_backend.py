"""Offline D-Bus doubles only; never change host/device networking."""
import asyncio
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from ai_cat_controller.provisioning.nm_backend import (
    AP_ADDRESS, ROOT, NetworkManagerBackend, NetworkManagerDBus, ap_settings, station_settings,
)
from ai_cat_controller.provisioning.protocol import NetworkCredentials, ProvisioningError


class Operations:
    def __init__(self):
        self.calls = []
        self.ap_ready = self.station_ready = True
        self.fail_save = self.fail_commit = self.fail_rollback = self.fail_autoconnect = False

    def checkpoint(self, timeout):
        self.calls.append(('checkpoint', timeout))
        return ROOT + '/Checkpoint/1'

    def activate(self, settings, *, temporary):
        self.calls.append(('activate', settings, temporary))
        return '/new-profile', '/ap-active' if temporary else '/station-active'

    def activated(self, active, *, ap):
        return self.ap_ready if ap else self.station_ready

    def persist(self, connection):
        self.calls.append(('persist', connection))
        if self.fail_save:
            raise RuntimeError('synthetic-private-save-error')

    def commit(self, checkpoint):
        self.calls.append(('commit', checkpoint))
        if self.fail_commit:
            raise RuntimeError('synthetic-private-commit-error')

    def enable_autoconnect(self, connection, settings):
        self.calls.append(('enable_autoconnect', connection))
        assert settings['connection']['autoconnect'] is False
        if self.fail_autoconnect:
            raise RuntimeError('synthetic-private-auto-error')

    def rollback(self, checkpoint):
        self.calls.append(('rollback', checkpoint))
        if self.fail_rollback:
            raise RuntimeError('synthetic-private-rollback-error')

    def close(self):
        self.calls.append(('close',))


def backend(ops):
    now = [0.0]
    async def sleep(seconds):
        now[0] += seconds
    return NetworkManagerBackend(ops, clock=lambda: now[0], sleeper=sleep)


def credentials():
    return NetworkCredentials.validate({'ssid': '测试网络', 'password': 'synthetic-password', 'security': 'wpa2-personal'})


def test_construction_has_no_networking_or_dbus_side_effects():
    ops = Operations()
    assert backend(ops).state == 'idle' and ops.calls == []
    bus = NetworkManagerDBus()
    assert bus._bus is None and NetworkManagerBackend().state == 'idle'


def test_ap_is_fixed_ephemeral_wpa2_and_never_a_client_supplied_address():
    settings = ap_settings('AI-CAT-ABC123', 'A' * 24)
    assert settings['connection']['interface-name'] == 'wlan0'
    assert settings['connection']['autoconnect'] is False
    assert settings['connection']['permissions'] == ['user:root:']
    assert settings['802-11-wireless']['mode'] == 'ap'
    assert settings['802-11-wireless-security']['proto'] == ['rsn']
    assert settings['ipv4']['address-data'] == [{'address': AP_ADDRESS, 'prefix': 24}]
    assert settings['ipv6']['method'] == 'disabled'


@pytest.mark.parametrize('password', ['88888888', 'A' * 63])
def test_ap_accepts_eight_character_test_password_without_weakening_wpa2_settings(password):
    settings = ap_settings('AI-CAT-ABC123', password)
    assert settings['802-11-wireless-security']['psk'] == password
    assert settings['802-11-wireless-security']['key-mgmt'] == 'wpa-psk'
    assert settings['802-11-wireless-security']['proto'] == ['rsn']
    assert settings['802-11-wireless-security']['pairwise'] == ['ccmp']
    assert settings['connection']['autoconnect'] is False


@pytest.mark.parametrize('password', ['8' * 7, '8' * 64, '8888888\n', '八' * 8, ' ' * 8])
def test_short_long_or_non_alphanumeric_ap_password_is_still_rejected(password):
    with pytest.raises(ProvisioningError):
        ap_settings('AI-CAT-ABC123', password)


@pytest.mark.parametrize('ssid,password', [('other', 'A' * 24), ('AI-CAT-ABC123', 'short'),
                                          ('AI-CAT-ABC123', 'A' * 24 + '\n')])
def test_invalid_ap_input_cannot_start_checkpoint(ssid, password):
    with pytest.raises(ProvisioningError):
        ap_settings(ssid, password)


def test_station_credentials_are_revalidated_and_not_used_as_profile_id():
    settings = station_settings(credentials())
    assert settings['802-11-wireless']['ssid'] == '测试网络'.encode()
    assert settings['802-11-wireless-security']['psk'] == 'synthetic-password'
    assert settings['connection']['id'].startswith('ai-cat-network-')
    assert '测试网络' not in settings['connection']['id']
    assert settings['connection']['autoconnect'] is False
    with pytest.raises(ProvisioningError):
        station_settings(NetworkCredentials('network', 'short'))


@pytest.mark.asyncio
async def test_one_explicit_window_then_save_and_checkpoint_commit_in_order():
    ops = Operations()
    subject = backend(ops)
    await subject.begin('AI-CAT-ABC123', 'A' * 24)
    assert subject.state == 'open'
    assert [call[0] for call in ops.calls] == ['checkpoint', 'activate']
    assert ops.calls[1][2] is True
    assert ops.calls[0] == ('checkpoint', 270)
    assert await subject(credentials(), 'request-one') == 'connected'
    assert [call[0] for call in ops.calls] == ['checkpoint', 'activate', 'activate', 'persist', 'commit', 'enable_autoconnect']
    assert ops.calls[2][2] is False
    with pytest.raises(ProvisioningError):
        await subject(credentials(), 'request-two')
    await subject.close()
    assert not any(call[0] == 'rollback' for call in ops.calls)


@pytest.mark.asyncio
async def test_failed_association_restores_original_without_persisting_new_profile():
    ops = Operations()
    ops.station_ready = False
    subject = backend(ops)
    await subject.begin('AI-CAT-ABC123', 'A' * 24)
    assert await subject(credentials(), 'one') == 'failed'
    assert subject.state == 'failed'
    assert [call[0] for call in ops.calls] == ['checkpoint', 'activate', 'activate', 'rollback']
    await subject.close()


@pytest.mark.asyncio
async def test_unverified_ap_activation_rolls_back_before_rejecting():
    ops = Operations()
    ops.ap_ready = False
    subject = backend(ops)
    with pytest.raises(ProvisioningError):
        await subject.begin('AI-CAT-ABC123', 'A' * 24)
    assert [call[0] for call in ops.calls] == ['checkpoint', 'activate', 'rollback']
    await subject.close()


@pytest.mark.parametrize('failure', ['fail_save', 'fail_commit', 'fail_rollback', 'fail_autoconnect'])
@pytest.mark.asyncio
async def test_unknown_or_persistence_failures_never_become_success_or_automatic_retry(failure):
    ops = Operations()
    subject = backend(ops)
    await subject.begin('AI-CAT-ABC123', 'A' * 24)
    setattr(ops, failure, True)
    if failure == 'fail_rollback':
        ops.station_ready = False
    assert await subject(credentials(), 'one') == 'unknown'
    with pytest.raises(ProvisioningError):
        await subject(credentials(), 'another')
    assert len([call for call in ops.calls if call[0] == 'activate']) == 2
    await subject.close()


@pytest.mark.asyncio
async def test_closing_unused_window_rolls_back_without_creating_station_profile():
    ops = Operations()
    subject = backend(ops)
    await subject.begin('AI-CAT-ABC123', 'A' * 24)
    await subject.close()
    assert [call[0] for call in ops.calls] == ['checkpoint', 'activate', 'rollback', 'close']


@pytest.mark.asyncio
async def test_cancelled_start_waits_for_real_dbus_work_before_rollback():
    ops = Operations()
    started, release = threading.Event(), threading.Event()
    original = ops.activate
    def activate(settings, *, temporary):
        started.set()
        assert release.wait(3)
        return original(settings, temporary=temporary)
    ops.activate = activate
    subject = backend(ops)
    task = asyncio.create_task(subject.begin('AI-CAT-ABC123', 'A' * 24))
    while not started.is_set():
        await asyncio.sleep(.001)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    closing = asyncio.create_task(subject.close())
    await asyncio.sleep(.01)
    assert not closing.done() and not any(call[0] == 'rollback' for call in ops.calls)
    release.set()
    await closing
    assert [call[0] for call in ops.calls] == ['checkpoint', 'activate', 'rollback', 'close']


@pytest.mark.asyncio
async def test_cancelled_apply_remains_tracked_until_save_and_commit_settle():
    ops = Operations()
    subject = backend(ops)
    await subject.begin('AI-CAT-ABC123', 'A' * 24)
    started, release = threading.Event(), threading.Event()
    original = ops.persist
    def persist(connection):
        started.set()
        assert release.wait(3)
        original(connection)
    ops.persist = persist
    task = asyncio.create_task(subject(credentials(), 'one'))
    while not started.is_set():
        await asyncio.sleep(.001)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    closing = asyncio.create_task(subject.close())
    await asyncio.sleep(.01)
    assert not closing.done()
    release.set()
    await closing
    assert subject.state == 'connected'
    assert [call[0] for call in ops.calls][-4:] == ['persist', 'commit', 'enable_autoconnect', 'close']


def test_backend_never_uses_subprocess_or_logs_or_getsecrets():
    source = Path(__file__).parents[2] / 'src/ai_cat_controller/provisioning/nm_backend.py'
    text = source.read_text()
    assert 'import subprocess' not in text and 'print(' not in text and 'logging' not in text
    assert '.GetSecrets(' not in text


@pytest.mark.asyncio
async def test_a_late_positive_dbus_read_cannot_cross_absolute_wait_deadline():
    now = [0.0]
    ops = Operations()
    def late_read(active, *, ap):
        now[0] = 61.0
        return True
    ops.activated = late_read
    subject = NetworkManagerBackend(ops, clock=lambda: now[0])
    assert await subject._wait('/station', ap=False, budget=60) is False
    assert not any(call[0] in {'persist', 'commit', 'enable_autoconnect'} for call in ops.calls)


class PrivateDBusError(RuntimeError):
    """Only an exact public D-Bus error name may be classified, never printed."""
    def __init__(self, name):
        super().__init__('PRIVATE_EXCEPTION 测试网络 synthetic-password')
        self.name = name

    def get_dbus_name(self):
        return self.name

    def __str__(self):
        raise AssertionError('private diagnostic errors must not be stringified')


class DiagnosticOperations(Operations):
    def __init__(self, now, stage, error, *, rollback_error=False):
        super().__init__()
        self.now, self.stage, self.error = now, stage, error
        self.fail_rollback = rollback_error

    def _fail(self, stage):
        if self.stage == stage:
            self.now[0] += 3.0
            raise self.error

    def activate(self, settings, *, temporary):
        result = super().activate(settings, temporary=temporary)
        if not temporary:
            self._fail('ACTIVATE')
        return result

    def activated(self, active, *, ap):
        if not ap:
            self._fail('VERIFY_ASSOCIATION')
        return super().activated(active, ap=ap)

    def persist(self, connection):
        super().persist(connection)
        self._fail('PERSIST')

    def commit(self, checkpoint):
        super().commit(checkpoint)
        self._fail('COMMIT')

    def enable_autoconnect(self, connection, settings):
        super().enable_autoconnect(connection, settings)
        self._fail('AUTOCONNECT')

    def rollback(self, checkpoint):
        # Restoring the old link must not replace the initial failing stage
        # or add rollback latency to that operation's reported duration.
        self.now[0] += 29.0
        return super().rollback(checkpoint)


@pytest.mark.parametrize('stage', ['ACTIVATE', 'VERIFY_ASSOCIATION', 'PERSIST', 'COMMIT', 'AUTOCONNECT'])
@pytest.mark.parametrize('rollback_error', [False, True])
@pytest.mark.asyncio
async def test_private_diagnostics_capture_original_stage_before_rollback_without_changing_result(stage, rollback_error, capsys):
    now = [0.0]
    ops = DiagnosticOperations(now, stage, PrivateDBusError('org.freedesktop.DBus.Error.NoReply'), rollback_error=rollback_error)
    subject = NetworkManagerBackend(ops, clock=lambda: now[0])
    assert subject.last_diagnostic is None
    await subject.begin('AI-CAT-ABC123', 'A' * 24)
    assert subject.last_diagnostic is None
    assert await subject(credentials(), 'diagnostic-request') == 'unknown'
    expected_rollback = 'NOT_AVAILABLE' if stage == 'AUTOCONNECT' else 'FAILED' if rollback_error else 'SUCCESS'
    expected = {'stage': stage, 'cause': 'TIMEOUT', 'elapsed_ms': 3000, 'rollback': expected_rollback}
    assert subject.last_diagnostic == expected
    assert type(subject.last_diagnostic['elapsed_ms']) is int
    copied = subject.last_diagnostic
    copied['cause'] = 'PRIVATE_MUTATED_CAUSE'
    copied['password'] = 'synthetic-password'
    assert subject.last_diagnostic == expected
    with pytest.raises(AttributeError):
        subject.last_diagnostic = copied
    names = [call[0] for call in ops.calls]
    assert names[:3] == ['checkpoint', 'activate', 'activate']
    assert names.count('activate') == 2
    assert ('rollback' in names) is (stage != 'AUTOCONNECT')
    if stage != 'AUTOCONNECT':
        assert names[-1] == 'rollback'
    await subject.close()
    assert subject.last_diagnostic == expected
    captured = capsys.readouterr()
    assert captured.out == captured.err == ''


@pytest.mark.parametrize('rollback_error', [False, True])
@pytest.mark.asyncio
async def test_association_timeout_records_original_cause_and_preserves_failed_or_unknown_rollback_result(rollback_error):
    now = [0.0]
    async def sleep(seconds):
        now[0] += seconds
    ops = DiagnosticOperations(now, None, RuntimeError('PRIVATE_UNUSED'), rollback_error=rollback_error)
    ops.station_ready = False
    subject = NetworkManagerBackend(ops, clock=lambda: now[0], sleeper=sleep)
    await subject.begin('AI-CAT-ABC123', 'A' * 24)
    assert await subject(credentials(), 'association-timeout') == ('unknown' if rollback_error else 'failed')
    assert subject.last_diagnostic == {'stage': 'VERIFY_ASSOCIATION', 'cause': 'ASSOCIATION_TIMEOUT', 'elapsed_ms': 60000, 'rollback': 'FAILED' if rollback_error else 'SUCCESS'}
    assert [call[0] for call in ops.calls] == ['checkpoint', 'activate', 'activate', 'rollback']
    await subject.close()


@pytest.mark.asyncio
async def test_successful_window_has_no_diagnostic_and_keeps_existing_commit_order():
    ops = Operations()
    subject = backend(ops)
    assert subject.last_diagnostic is None
    await subject.begin('AI-CAT-ABC123', 'A' * 24)
    assert await subject(credentials(), 'healthy-request') == 'connected'
    assert subject.last_diagnostic is None
    await subject.close()
    assert subject.last_diagnostic is None
    assert [call[0] for call in ops.calls] == ['checkpoint', 'activate', 'activate', 'persist', 'commit', 'enable_autoconnect', 'close']


@pytest.mark.parametrize('name,cause', [
    ('org.freedesktop.DBus.Error.NoReply', 'TIMEOUT'),
    ('org.freedesktop.DBus.Error.Timeout', 'TIMEOUT'),
    ('org.freedesktop.DBus.Error.TimedOut', 'TIMEOUT'),
    ('org.freedesktop.DBus.Error.AccessDenied', 'PERMISSION'),
    ('org.freedesktop.DBus.Error.AuthFailed', 'PERMISSION'),
    ('org.freedesktop.NetworkManager.PermissionDenied', 'PERMISSION'),
    ('org.freedesktop.NetworkManager.Settings.PermissionDenied', 'PERMISSION'),
    ('org.freedesktop.DBus.Error.InvalidArgs', 'INVALID_ARGS'),
    ('org.freedesktop.DBus.Error.NoReply.PRIVATE_ERROR', 'OTHER'),
    ('org.freedesktop.DBus.Error.UnknownTimeout', 'OTHER'),
    ('PRIVATE_ERROR synthetic-password', 'OTHER'),
    (None, 'OTHER'), (4, 'OTHER'), ([], 'OTHER'),
])
def test_diagnostic_cause_uses_only_exact_dbus_name_whitelist_without_stringifying_exception(name, cause, capsys):
    from ai_cat_controller.provisioning.nm_backend import diagnostic_cause
    assert diagnostic_cause(PrivateDBusError(name)) == cause
    captured = capsys.readouterr()
    assert captured.out == captured.err == ''


@pytest.mark.parametrize('error,cause', [
    (TimeoutError('PRIVATE_TIMEOUT'), 'TIMEOUT'),
    (PermissionError('PRIVATE_PERMISSION'), 'PERMISSION'),
    (asyncio.CancelledError('PRIVATE_CANCELLED'), 'CANCELLED'),
    (ValueError('PRIVATE_INVALID'), 'OTHER'),
])
def test_diagnostic_cause_classifies_public_python_exception_types_without_text(error, cause):
    from ai_cat_controller.provisioning.nm_backend import diagnostic_cause
    assert diagnostic_cause(error) == cause


@pytest.mark.parametrize('variant', ['method-raises', 'property-raises', 'string-subclass', 'untrusted-object'])
def test_malicious_dbus_name_getters_and_values_are_reduced_to_other(variant):
    from ai_cat_controller.provisioning.nm_backend import diagnostic_cause
    class Untrusted:
        def __hash__(self):
            raise AssertionError('untrusted native values must not be hashed')
        def __str__(self):
            raise AssertionError('untrusted native values must not be rendered')
    class UntrustedString(str):
        def __hash__(self):
            raise AssertionError('native name subclasses must not be hashed')
    class BrokenError(RuntimeError):
        @property
        def get_dbus_name(self):
            if variant == 'property-raises':
                raise RuntimeError('PRIVATE_GETTER')
            def getter():
                if variant == 'method-raises':
                    raise RuntimeError('PRIVATE_METHOD')
                if variant == 'string-subclass':
                    return UntrustedString('org.freedesktop.DBus.Error.NoReply')
                return Untrusted()
            return getter
    assert diagnostic_cause(BrokenError('PRIVATE_EXCEPTION')) == 'OTHER'


@pytest.mark.parametrize('value,expected', [(-1.0, 0), (0.0009, 0), (0.001, 1),
                                           (3601.0, 3600000), (float('inf'), 0),
                                           (float('nan'), 0), ('PRIVATE_CLOCK', 0)])
def test_diagnostic_elapsed_is_bounded_nonnegative_integer_for_untrusted_clock_results(value, expected):
    subject = NetworkManagerBackend(Operations(), clock=lambda: value)
    result = subject._elapsed_ms(0.0)
    assert type(result) is int and result == expected


def test_diagnostic_clock_exception_is_unavailable_timing_not_a_replacement_failure():
    def clock():
        raise RuntimeError('PRIVATE_CLOCK_EXCEPTION')
    subject = NetworkManagerBackend(Operations(), clock=clock)
    assert subject._elapsed_ms(0.0) == 0


def test_only_profile_disk_writes_receive_longer_bounded_dbus_deadlines():
    calls, proxies = [], []
    def record(name, result=None):
        def method(*args, **kwargs):
            calls.append((name, args, kwargs))
            return result
        return method
    interface = SimpleNamespace(Update2=record('Update2'))
    subject = NetworkManagerDBus()
    subject._open = lambda: None
    subject._device = '/device'
    subject._manager = SimpleNamespace(
        CheckpointCreate=record('CheckpointCreate', ROOT + '/Checkpoint/1'),
        AddAndActivateConnection2=record('AddAndActivateConnection2', ('/new', '/active', {})),
        CheckpointDestroy=record('CheckpointDestroy'),
        CheckpointRollback=record('CheckpointRollback', {'/device': 0}),
    )
    subject._dbus = SimpleNamespace(UInt32=lambda value: value,
        Dictionary=lambda value, **kwargs: value, Interface=lambda *args: interface)
    def proxy(*args, **kwargs):
        proxies.append(kwargs)
        return '/synthetic-object'
    subject._bus = SimpleNamespace(get_object=proxy)
    subject._settings = lambda value: value
    checkpoint = subject.checkpoint(260)
    settings = station_settings(credentials())
    assert subject.activate(settings, temporary=False) == ('/new', '/active')
    subject.persist('/new')
    subject.commit(checkpoint)
    subject.enable_autoconnect('/new', settings)
    subject.rollback(checkpoint)
    assert [call[0] for call in calls] == ['CheckpointCreate', 'AddAndActivateConnection2', 'Update2', 'CheckpointDestroy', 'Update2', 'CheckpointRollback']
    assert all(call[2] == {'timeout': 8 if call[0] == 'Update2' else 3} for call in calls)
    assert proxies == [{'introspect': False}] * 2
    assert calls[0][1] == (['/device'], 260, 0)
    assert calls[1][1][3] == {'persist': 'memory', 'bind-activation': 'none'}


def delayed_dbus_operations(now, *, disk_seconds=4, gate=None):
    """Real NM adapter methods, synthetic synchronous D-Bus replies only."""
    calls = []
    def record(name, result=None):
        def method(*args, **kwargs):
            calls.append((name, kwargs.get('timeout')))
            return result
        return method
    def update(settings, flags, args, *, timeout):
        calls.append(('Update2', timeout))
        if gate is not None:
            gate()
        count = sum(name == 'Update2' for name, _ in calls)
        duration = disk_seconds[count - 1] if type(disk_seconds) is tuple else disk_seconds
        now[0] += min(duration, timeout)
        if duration > timeout:
            raise PrivateDBusError('org.freedesktop.DBus.Error.NoReply')
        return {}
    interface = SimpleNamespace(Update2=update)
    subject = NetworkManagerDBus()
    subject._open = lambda: None
    subject._device = '/device'
    subject._manager = SimpleNamespace(
        CheckpointCreate=record('CheckpointCreate', ROOT + '/Checkpoint/1'),
        AddAndActivateConnection2=record('AddAndActivateConnection2', ('/new', '/active', {})),
        CheckpointDestroy=record('CheckpointDestroy'),
        CheckpointRollback=record('CheckpointRollback', {'/device': 0}),
    )
    subject._dbus = SimpleNamespace(UInt32=lambda value: value,
        Dictionary=lambda value, **kwargs: value, Interface=lambda *args: interface)
    def proxy(*args, **kwargs):
        return '/synthetic-object'
    subject._bus = SimpleNamespace(get_object=proxy, close=record('close'))
    subject._settings = lambda value: value
    subject.activated = lambda active, *, ap: True
    return subject, calls


@pytest.mark.asyncio
async def test_four_second_profile_writes_commit_once_without_early_rollback_or_retry():
    now = [0.0]
    ops, calls = delayed_dbus_operations(now, disk_seconds=4)
    subject = NetworkManagerBackend(ops, clock=lambda: now[0])
    await subject.begin('AI-CAT-ABC123', 'A' * 24)
    assert await subject(credentials(), 'slow-disk') == 'connected'
    assert [name for name, _ in calls] == ['CheckpointCreate', 'AddAndActivateConnection2',
        'AddAndActivateConnection2', 'Update2', 'CheckpointDestroy', 'Update2']
    assert [timeout for name, timeout in calls if name == 'Update2'] == [8, 8]
    assert subject.last_diagnostic is None
    await subject.close()


@pytest.mark.asyncio
async def test_genuine_disk_deadline_keeps_unknown_and_never_commits_enables_or_retries():
    now = [0.0]
    ops, calls = delayed_dbus_operations(now, disk_seconds=9)
    subject = NetworkManagerBackend(ops, clock=lambda: now[0])
    await subject.begin('AI-CAT-ABC123', 'A' * 24)
    assert await subject(credentials(), 'too-slow-disk') == 'unknown'
    assert subject.last_diagnostic == {'stage': 'PERSIST', 'cause': 'TIMEOUT', 'elapsed_ms': 8000, 'rollback': 'SUCCESS'}
    assert [name for name, _ in calls] == ['CheckpointCreate', 'AddAndActivateConnection2',
        'AddAndActivateConnection2', 'Update2', 'CheckpointRollback']
    with pytest.raises(ProvisioningError):
        await subject(credentials(), 'no-second-send')
    await subject.close()


@pytest.mark.asyncio
async def test_autoconnect_disk_timeout_after_commit_never_claims_rollback_or_replays_save():
    now = [0.0]
    ops, calls = delayed_dbus_operations(now, disk_seconds=(4, 9))
    subject = NetworkManagerBackend(ops, clock=lambda: now[0])
    await subject.begin('AI-CAT-ABC123', 'A' * 24)
    assert await subject(credentials(), 'post-commit-timeout') == 'unknown'
    assert subject.last_diagnostic == {'stage': 'AUTOCONNECT', 'cause': 'TIMEOUT', 'elapsed_ms': 8000, 'rollback': 'NOT_AVAILABLE'}
    assert [name for name, _ in calls] == ['CheckpointCreate', 'AddAndActivateConnection2',
        'AddAndActivateConnection2', 'Update2', 'CheckpointDestroy', 'Update2']
    await subject.close()
    assert [name for name, _ in calls][-1] == 'close'


@pytest.mark.asyncio
async def test_cancel_during_real_adapter_update_waits_for_reply_before_commit_or_cleanup():
    now = [0.0]
    started, release = threading.Event(), threading.Event()
    def gate():
        started.set()
        assert release.wait(3)
    ops, calls = delayed_dbus_operations(now, disk_seconds=4, gate=gate)
    subject = NetworkManagerBackend(ops, clock=lambda: now[0])
    await subject.begin('AI-CAT-ABC123', 'A' * 24)
    task = asyncio.create_task(subject(credentials(), 'cancelled-waiter'))
    async with asyncio.timeout(2):
        while not started.is_set():
            await asyncio.sleep(.001)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    closing = asyncio.create_task(subject.close())
    await asyncio.sleep(.01)
    assert not closing.done()
    assert [name for name, _ in calls][-1] == 'Update2'
    release.set()
    await closing
    assert subject.state == 'connected'
    assert [name for name, _ in calls][-4:] == ['Update2', 'CheckpointDestroy', 'Update2', 'close']
    assert not any(name == 'CheckpointRollback' for name, _ in calls)


def test_checkpoint_tail_budget_covers_success_and_last_association_poll_without_extending_acceptance():
    from ai_cat_controller.provisioning import nm_backend as module
    from ai_cat_controller.local_gateway.provisioning import WINDOW_SECONDS
    call, write, association = module.DBUS_CALL_SECONDS, module.PROFILE_WRITE_SECONDS, module.ASSOCIATION_SECONDS
    cue = module.CUE_SETTLE_SECONDS
    success = cue + call + association + write + call + write
    # An unsuccessful last poll may straddle its deadline with five fixed Get
    # calls, then the one original rollback. No credential retry is budgeted.
    late_poll_failure = cue + call + association + 5 * call + call
    assert (success, late_poll_failure) == (85, 84)
    assert max(success, late_poll_failure) < module.CHECKPOINT_SETTLE_SECONDS == 90
    assert WINDOW_SECONDS == 180


def test_current_association_gets_skip_implicit_introspection_and_remain_five_fixed_reads():
    reads, proxies = [], []
    values = {'ActiveConnection': '/active', 'State': 100, 'Ip4Config': '/ip4',
        'AddressData': [{'address': '192.0.2.1'}]}
    def get(interface, field, *, timeout):
        reads.append((field, timeout))
        return 2 if interface.endswith('.Connection.Active') and field == 'State' else values[field]
    subject = NetworkManagerDBus()
    subject._open = lambda: None
    subject._device = '/device'
    subject._dbus = SimpleNamespace(Interface=lambda *args: SimpleNamespace(Get=get))
    def proxy(*args, **kwargs):
        proxies.append(kwargs)
        return '/synthetic'
    subject._bus = SimpleNamespace(get_object=proxy)
    assert subject.activated('/active', ap=False) is True
    assert len(reads) == 5 and all(timeout == 3 for _, timeout in reads)
    assert proxies == [{'introspect': False}] * 3
