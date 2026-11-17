"""Opt-in, once-per-boot offline first-setup entry. Never a reconnect loop.

Only the private commissioning policy can enable it. It does not change GPIO,
display or voice services. Already-connected/connecting Wi-Fi, unknown previous
work, unavailable NM evidence, or failed owner authority all cause no action.
"""
import asyncio
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import stat

from .configuration import private_identity
from .protocol import parse_json, require

_BOOT_ID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')


def _claim_boot(state_dir, boot_id):
    """Consume one kernel-boot evaluation before delay, even if already online.

    The fixed private marker survives daemon restarts. A malformed/unreadable
    marker is never repaired; lock contention and failed durability fail closed.
    """
    require(type(boot_id) is str and _BOOT_ID.fullmatch(boot_id))
    state_dir = Path(state_dir)
    directory = private_identity(state_dir, directory=True)[:2]
    marker, lock_path = state_dir / 'offline-first-boot.json', state_dir / 'offline-first-boot.lock'
    lock_fd, temporary = None, None
    try:
        lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        lock_info = os.fstat(lock_fd)
        require(stat.S_ISREG(lock_info.st_mode) and stat.S_IMODE(lock_info.st_mode) == 0o600
            and lock_info.st_uid == os.geteuid() and lock_info.st_nlink == 1 and lock_info.st_size == 0)
        require(private_identity(lock_path)[:2] == (lock_info.st_dev, lock_info.st_ino))
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        previous = None
        if marker.exists() or marker.is_symlink():
            previous = private_identity(marker)
            require(0 < previous[2] <= 256 and stat.S_IMODE(marker.stat().st_mode) == 0o600)
            marker_fd = os.open(marker, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            with os.fdopen(marker_fd, 'rb') as source:
                marker_info = os.fstat(source.fileno())
                require(previous[:2] == (marker_info.st_dev, marker_info.st_ino))
                value = parse_json(source.read(257), maximum=256)
            require(private_identity(marker) == previous and set(value) == {'v', 'bootId'}
                and type(value['v']) is int and value['v'] == 1
                and type(value['bootId']) is str and _BOOT_ID.fullmatch(value['bootId']))
            if value['bootId'] == boot_id:
                return False
        raw = json.dumps({'v': 1, 'bootId': boot_id}, separators=(',', ':')).encode('ascii')
        temporary = state_dir / ('.offline-first-boot.' + secrets.token_hex(16) + '.tmp')
        temp_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        with os.fdopen(temp_fd, 'wb') as target:
            os.fchmod(target.fileno(), 0o600)
            target.write(raw)
            target.flush()
            os.fsync(target.fileno())
        require(private_identity(state_dir, directory=True)[:2] == directory)
        require((private_identity(marker) == previous) if previous is not None
            else not marker.exists() and not marker.is_symlink())
        os.replace(temporary, marker)
        temporary = None
        directory_fd = os.open(state_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            require((os.fstat(directory_fd).st_dev, os.fstat(directory_fd).st_ino) == directory)
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return True
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass
        if lock_fd is not None:
            os.close(lock_fd)


def _disconnected_wlan():
    """Read fixed NM properties; exceptions are not evidence of disconnection."""
    import dbus
    bus = dbus.SystemBus()
    nm = dbus.Interface(bus.get_object('org.freedesktop.NetworkManager', '/org/freedesktop/NetworkManager'),
                        'org.freedesktop.NetworkManager')
    props = dbus.Interface(bus.get_object('org.freedesktop.NetworkManager', '/org/freedesktop/NetworkManager'),
                           'org.freedesktop.DBus.Properties')
    path = nm.GetDeviceByIpIface('wlan0', timeout=3)
    device = dbus.Interface(bus.get_object('org.freedesktop.NetworkManager', path), 'org.freedesktop.DBus.Properties')
    return (bool(props.Get('org.freedesktop.NetworkManager', 'WirelessEnabled', timeout=3))
        and int(device.Get('org.freedesktop.NetworkManager.Device', 'State', timeout=3)) == 30
        and bool(device.Get('org.freedesktop.NetworkManager.Device', 'Managed', timeout=3))
        and str(device.Get('org.freedesktop.NetworkManager.Device', 'ActiveConnection', timeout=3)) == '/')


class OfflineBootstrap:
    def __init__(self, coordinator, verify_authority, *, disconnected=_disconnected_wlan,
                 boot_id=None, delay=20, claim_boot=None):
        self.coordinator, self.verify_authority = coordinator, verify_authority
        self.disconnected, self.delay = disconnected, delay
        self.boot_id = boot_id
        self.claim_boot, self._seen_boots = claim_boot, set()
        self._stop = asyncio.Event()

    async def run(self):
        try:
            boot_id = self.boot_id if self.boot_id is not None else Path('/proc/sys/kernel/random/boot_id').read_text().strip()
            require(type(boot_id) is str and _BOOT_ID.fullmatch(boot_id))
            if self._stop.is_set() or boot_id in self._seen_boots:
                return
            self._seen_boots.add(boot_id)
            # Online/connecting/skipped startup also consumes this kernel boot.
            # A later systemd restart after an ordinary Wi-Fi drop must not turn
            # into a first offline-setup attempt in the same running boot.
            if self.claim_boot is not None and await asyncio.wait_for(
                    asyncio.to_thread(self.claim_boot, boot_id), 5) is not True:
                return
            try:
                await asyncio.wait_for(self._stop.wait(), self.delay)
                return
            except asyncio.TimeoutError:
                pass
            if self._stop.is_set() or not await asyncio.wait_for(asyncio.to_thread(self.disconnected), 10):
                return
            await asyncio.wait_for(self.verify_authority(), 5)
            current = await self.coordinator.current_window()
            row = current['window']
            request_id = 'boot_' + boot_id.replace('-', '')
            # Same boot never creates a new window; unresolved work is never
            # archived, cleared or classified by inferred network connectivity.
            if row is not None and (row['requestId'] == request_id or
                    row['status'] in {'prepared','opening','open','accepted','connecting','unknown'}):
                return
            if self._stop.is_set() or not await asyncio.wait_for(asyncio.to_thread(self.disconnected), 10):
                return
            prepared = await self.coordinator.prepare(request_id, confirmation='open-local-provisioning')
            if prepared['status'] == 'prepared' and not self._stop.is_set():
                # The explicit local boot policy, rather than a public HTTP GET,
                # authorizes this entry. Every Wi-Fi credential still requires
                # the card holder's confirmed encrypted configure request.
                async def still_offline():
                    if self._stop.is_set() or not await asyncio.wait_for(asyncio.to_thread(self.disconnected), 10):
                        return False
                    await asyncio.wait_for(self.verify_authority(), 5)
                    return not self._stop.is_set()
                await self.coordinator.start(request_id, offline_guard=still_offline)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Missing DBus/ownership/ledger evidence must never trigger an AP.
            return

    async def aclose(self):
        self._stop.set()


def create_offline_bootstrap(config, coordinator, verify_authority):
    policy = config.state_dir / 'offline-first-setup.json'
    if not policy.exists() and not policy.is_symlink():
        return None
    identity = private_identity(policy)
    require(0 < identity[2] <= 256)
    value = parse_json(policy.read_bytes(), maximum=256)
    require(private_identity(policy) == identity and set(value) == {'v','enabled'}
        and type(value['v']) is int and value['v'] == 1 and type(value['enabled']) is bool)
    return OfflineBootstrap(coordinator, verify_authority,
        claim_boot=lambda boot_id: _claim_boot(config.state_dir, boot_id)) if value['enabled'] else None
