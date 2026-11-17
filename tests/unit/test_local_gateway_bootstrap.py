from types import SimpleNamespace
import asyncio
import json
import pytest
from ai_cat_controller.local_gateway.bootstrap import OfflineBootstrap, create_offline_bootstrap


class Coordinator:
    def __init__(self, row=None):
        self.row, self.calls = row, []
    async def current_window(self):
        return {'window':self.row}
    async def prepare(self, request, confirmation):
        self.calls.append(('prepare',request,confirmation))
        return {'status':'prepared'}
    async def start(self, request, offline_guard=None):
        if offline_guard is not None:
            assert await offline_guard() is True
        self.calls.append(('start',request))


async def authorized():
    pass


@pytest.mark.asyncio
async def test_offline_policy_once_per_boot_only():
    c = Coordinator()
    boot = OfflineBootstrap(c, authorized, disconnected=lambda:True, boot_id='12345678-1234-1234-1234-123456789abc', delay=0)
    await boot.run()
    assert [call[0] for call in c.calls] == ['prepare','start']
    c.row = {'requestId':c.calls[0][1],'status':'not_sent'}
    await boot.run()
    assert len(c.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['prepared','opening','open','accepted','connecting','unknown'])
async def test_pending_work_is_never_cleared_or_reopened(status):
    c = Coordinator({'requestId':'old_request','status':status})
    await OfflineBootstrap(c, authorized, disconnected=lambda:True,
        boot_id='12345678-1234-1234-1234-123456789abc', delay=0).run()
    assert not c.calls


@pytest.mark.asyncio
async def test_connected_connecting_or_failed_evidence_does_not_open():
    for check in (lambda:False, lambda: (_ for _ in ()).throw(ValueError())):
        c = Coordinator()
        await OfflineBootstrap(c, authorized, disconnected=check, delay=0).run()
        assert not c.calls


@pytest.mark.asyncio
async def test_second_check_prevents_interrupting_reconnection():
    c = Coordinator()
    states = iter([True,False])
    await OfflineBootstrap(c, authorized, disconnected=lambda:next(states),
        boot_id='12345678-1234-1234-1234-123456789abc', delay=0).run()
    assert not c.calls


@pytest.mark.asyncio
async def test_missing_owner_authority_fails_closed():
    c = Coordinator()
    async def denied():
        raise ValueError()
    await OfflineBootstrap(c, denied, disconnected=lambda:True,
        boot_id='12345678-1234-1234-1234-123456789abc', delay=0).run()
    assert not c.calls


def test_no_implicit_boot_policy(tmp_path):
    config = SimpleNamespace(state_dir=tmp_path)
    assert create_offline_bootstrap(config, Coordinator(), authorized) is None


def configured_boot(tmp_path, coordinator, *, connected, boot_id):
    tmp_path.chmod(0o700)
    policy = tmp_path / 'offline-first-setup.json'
    if not policy.exists():
        policy.write_text('{"v":1,"enabled":true}')
        policy.chmod(0o600)
    boot = create_offline_bootstrap(SimpleNamespace(state_dir=tmp_path), coordinator, authorized)
    boot.disconnected = lambda: not connected
    boot.boot_id, boot.delay = boot_id, 0
    return boot


@pytest.mark.asyncio
async def test_connected_start_consumes_kernel_boot_before_sidecar_restart(tmp_path):
    kernel = '12345678-1234-1234-1234-123456789abc'
    first = Coordinator()
    await configured_boot(tmp_path, first, connected=True, boot_id=kernel).run()
    assert not first.calls
    marker = tmp_path / 'offline-first-boot.json'
    assert json.loads(marker.read_bytes()) == {'v':1,'bootId':kernel}
    assert marker.stat().st_mode & 0o777 == 0o600
    after_restart = Coordinator()
    await configured_boot(tmp_path, after_restart, connected=False, boot_id=kernel).run()
    assert not after_restart.calls


@pytest.mark.asyncio
async def test_next_kernel_boot_may_attempt_once_after_connected_previous_boot(tmp_path):
    await configured_boot(tmp_path, Coordinator(), connected=True,
        boot_id='12345678-1234-1234-1234-123456789abc').run()
    next_boot = '23456789-1234-1234-1234-123456789abc'
    coordinator = Coordinator()
    await configured_boot(tmp_path, coordinator, connected=False, boot_id=next_boot).run()
    assert [call[0] for call in coordinator.calls] == ['prepare','start']
    restart = Coordinator()
    await configured_boot(tmp_path, restart, connected=False, boot_id=next_boot).run()
    assert not restart.calls


@pytest.mark.asyncio
async def test_boot_is_claimed_before_startup_delay_and_network_observation(tmp_path):
    coordinator = Coordinator()
    boot = configured_boot(tmp_path, coordinator, connected=False,
        boot_id='12345678-1234-1234-1234-123456789abc')
    boot.delay = 60
    checked = []
    boot.disconnected = lambda: checked.append(True) or True
    task = asyncio.create_task(boot.run())
    try:
        for _ in range(100):
            if (tmp_path / 'offline-first-boot.json').exists():
                break
            await asyncio.sleep(0.005)
        assert (tmp_path / 'offline-first-boot.json').exists()
        assert not checked and not coordinator.calls
    finally:
        await boot.aclose()
        await asyncio.wait_for(task, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize('bad', ['json','shape','mode','symlink','hardlink'])
async def test_bad_or_nonprivate_boot_marker_refuses_ap_without_repair(tmp_path, bad):
    kernel = '12345678-1234-1234-1234-123456789abc'
    coordinator = Coordinator()
    boot = configured_boot(tmp_path, coordinator, connected=False, boot_id=kernel)
    marker = tmp_path / 'offline-first-boot.json'
    content = b'bad' if bad == 'json' else b'{"v":1,"bootId":"23456789-1234-1234-1234-123456789abc"}'
    if bad == 'shape':
        content = b'{"v":1,"bootId":"not-a-kernel-boot"}'
    if bad == 'symlink':
        target = tmp_path / 'target.json'
        target.write_bytes(content)
        target.chmod(0o600)
        marker.symlink_to(target)
    else:
        marker.write_bytes(content)
        marker.chmod(0o644 if bad == 'mode' else 0o600)
        if bad == 'hardlink':
            (tmp_path / 'other.json').hardlink_to(marker)
    await boot.run()
    assert not coordinator.calls
    assert marker.read_bytes() == content


def test_constructing_enabled_bootstrap_does_not_create_claim(tmp_path):
    boot = configured_boot(tmp_path, Coordinator(), connected=False,
        boot_id='12345678-1234-1234-1234-123456789abc')
    assert boot is not None
    assert not (tmp_path / 'offline-first-boot.json').exists()
    assert not (tmp_path / 'offline-first-boot.lock').exists()
