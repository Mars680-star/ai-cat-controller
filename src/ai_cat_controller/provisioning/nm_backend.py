"""Explicit NetworkManager D-Bus operations for one temporary SoftAP window.

Construction/import never changes networking. The original saved connection is
not edited. A NetworkManager-owned timed checkpoint restores it if provisioning
fails or the operator process dies. Passwords never enter argv, URLs or logs.
"""
import asyncio
import re
import time
import uuid

from .protocol import AP_ADDRESS, NetworkCredentials, ProvisioningError, require

NM = 'org.freedesktop.NetworkManager'
ROOT = '/org/freedesktop/NetworkManager'
PROPERTIES = 'org.freedesktop.DBus.Properties'
DIAGNOSTIC_MAX_MS = 3_600_000
DBUS_CALL_SECONDS = 3
PROFILE_WRITE_SECONDS = 8
ASSOCIATION_SECONDS = 60
CUE_SETTLE_SECONDS = 3
# Acceptance remains 180s (or the explicitly selected CLI window). At its
# end, a single already accepted operation still needs settlement time:
# success <= cue3 + activate3 + association60 + save8 + commit3 + auto-save8 =85;
# failed final poll <= cue3 + activate3 + association60 + five Get calls*3
# + rollback3 =84. The 90s tail covers both, not another request or retry.
# Fixed Get/Update2 proxies skip implicit introspection so that those calls
# cannot add a separate, hidden D-Bus default timeout to this budget.
CHECKPOINT_SETTLE_SECONDS = 90
_DBUS_CAUSES = {
    'org.freedesktop.DBus.Error.NoReply': 'TIMEOUT',
    'org.freedesktop.DBus.Error.Timeout': 'TIMEOUT',
    'org.freedesktop.DBus.Error.TimedOut': 'TIMEOUT',
    'org.freedesktop.DBus.Error.AccessDenied': 'PERMISSION',
    'org.freedesktop.DBus.Error.AuthFailed': 'PERMISSION',
    'org.freedesktop.NetworkManager.PermissionDenied': 'PERMISSION',
    'org.freedesktop.NetworkManager.Settings.PermissionDenied': 'PERMISSION',
    'org.freedesktop.DBus.Error.InvalidArgs': 'INVALID_ARGS',
}


def diagnostic_cause(error):
    """Classify exact names only; never retain or render an exception's text."""
    if isinstance(error, asyncio.CancelledError):
        return 'CANCELLED'
    if isinstance(error, TimeoutError):
        return 'TIMEOUT'
    if isinstance(error, PermissionError):
        return 'PERMISSION'
    try:
        name = error.get_dbus_name()
        return _DBUS_CAUSES.get(name, 'OTHER') if type(name) is str else 'OTHER'
    except BaseException:
        return 'OTHER'


def ap_settings(ssid, password):
    require(type(ssid) is str and re.fullmatch(r'AI-CAT-[A-F0-9]{6}', ssid) is not None)
    # The local operator pilot accepts the requested eight-digit test password;
    # keep the WPA2 length bounds and ASCII-only generated-AP character set.
    require(type(password) is str and re.fullmatch(r'[A-Za-z0-9]{8,63}', password) is not None)
    return {
        'connection': {'id': 'ai-cat-provision-' + uuid.uuid4().hex, 'uuid': str(uuid.uuid4()),
                       'type': '802-11-wireless', 'interface-name': 'wlan0', 'autoconnect': False,
                       'permissions': ['user:root:']},
        '802-11-wireless': {'ssid': ssid.encode(), 'mode': 'ap', 'band': 'bg',
                           'security': '802-11-wireless-security'},
        '802-11-wireless-security': {'key-mgmt': 'wpa-psk', 'psk': password, 'psk-flags': 0,
                                    'proto': ['rsn'], 'pairwise': ['ccmp'], 'group': ['ccmp']},
        'ipv4': {'method': 'shared', 'address-data': [{'address': AP_ADDRESS, 'prefix': 24}]},
        'ipv6': {'method': 'disabled'},
    }


def station_settings(credentials):
    credentials = NetworkCredentials.validate(credentials.payload())
    return {
        'connection': {'id': 'ai-cat-network-' + uuid.uuid4().hex, 'uuid': str(uuid.uuid4()),
                       'type': '802-11-wireless', 'interface-name': 'wlan0', 'autoconnect': False,
                       'permissions': ['user:root:']},
        '802-11-wireless': {'ssid': credentials.ssid.encode(), 'mode': 'infrastructure',
                           'security': '802-11-wireless-security'},
        '802-11-wireless-security': {'key-mgmt': 'wpa-psk', 'psk': credentials.password, 'psk-flags': 0,
                                    'proto': ['rsn'], 'pairwise': ['ccmp'], 'group': ['ccmp']},
        'ipv4': {'method': 'auto'}, 'ipv6': {'method': 'auto'},
    }


class NetworkManagerDBus:
    """Lazy, private bus connection; no subprocess or secret introspection."""
    def __init__(self):
        self._dbus = self._bus = self._manager = self._device = None

    def _open(self):
        if self._bus is not None:
            return
        import dbus
        self._dbus = dbus
        self._bus = dbus.SystemBus(private=True)
        try:
            self._manager = dbus.Interface(self._bus.get_object(NM, ROOT), NM)
            self._device = self._manager.GetDeviceByIpIface('wlan0', timeout=DBUS_CALL_SECONDS)
            root = self._properties(ROOT)
            device = self._properties(self._device)
            require(bool(root.Get(NM, 'WirelessEnabled', timeout=DBUS_CALL_SECONDS)) and
                    bool(root.Get(NM, 'WirelessHardwareEnabled', timeout=DBUS_CALL_SECONDS)))
            require(int(device.Get(NM + '.Device', 'DeviceType', timeout=DBUS_CALL_SECONDS)) == 2 and
                    bool(device.Get(NM + '.Device', 'Managed', timeout=DBUS_CALL_SECONDS)))
            capabilities = int(device.Get(NM + '.Device.Wireless', 'WirelessCapabilities', timeout=DBUS_CALL_SECONDS))
            # NetworkManager's public NMDeviceWifiCapabilities: AP, RSN, CCMP.
            require(capabilities & 0x68 == 0x68)
        except BaseException:
            self.close()
            raise ProvisioningError() from None

    def _properties(self, path):
        return self._dbus.Interface(self._bus.get_object(NM, path, introspect=False), PROPERTIES)

    def checkpoint(self, timeout):
        self._open()
        return str(self._manager.CheckpointCreate([self._device], self._dbus.UInt32(timeout),
                                                  self._dbus.UInt32(0), timeout=DBUS_CALL_SECONDS))

    def _settings(self, settings):
        dbus = self._dbus
        result = {}
        for section, values in settings.items():
            typed = {}
            for key, value in values.items():
                if key == 'ssid':
                    value = dbus.ByteArray(value)
                elif key == 'address-data':
                    value = dbus.Array([dbus.Dictionary({'address': item['address'],
                                                        'prefix': dbus.UInt32(item['prefix'])}, signature='sv')
                                        for item in value], signature='a{sv}')
                elif type(value) is bool:
                    value = dbus.Boolean(value)
                elif type(value) is int:
                    value = dbus.UInt32(value)
                elif type(value) is list:
                    value = dbus.Array(value, signature='s')
                typed[key] = value
            result[section] = dbus.Dictionary(typed, signature='sv')
        return dbus.Dictionary(result, signature='sa{sv}')

    def activate(self, settings, *, temporary):
        self._open()
        options = {'persist': 'volatile' if temporary else 'memory',
                   'bind-activation': 'dbus-client' if temporary else 'none'}
        connection, active, _ = self._manager.AddAndActivateConnection2(
            self._settings(settings), self._device, '/',
            self._dbus.Dictionary(options, signature='sv'), timeout=DBUS_CALL_SECONDS)
        return str(connection), str(active)

    def activated(self, active, *, ap):
        self._open()
        device = self._properties(self._device)
        if str(device.Get(NM + '.Device', 'ActiveConnection', timeout=DBUS_CALL_SECONDS)) != active:
            return False
        if int(device.Get(NM + '.Device', 'State', timeout=DBUS_CALL_SECONDS)) != 100:
            return False
        properties = self._properties(active)
        if int(properties.Get(NM + '.Connection.Active', 'State', timeout=DBUS_CALL_SECONDS)) != 2:
            return False
        ipv4 = str(properties.Get(NM + '.Connection.Active', 'Ip4Config', timeout=DBUS_CALL_SECONDS))
        if ipv4 == '/':
            return False
        addresses = self._properties(ipv4).Get(NM + '.IP4Config', 'AddressData', timeout=DBUS_CALL_SECONDS)
        return bool(addresses) and (not ap or any(item.get('address') == AP_ADDRESS for item in addresses))

    def persist(self, connection):
        interface = self._dbus.Interface(self._bus.get_object(NM, connection, introspect=False), NM + '.Settings.Connection')
        # Empty settings reuse the existing complete in-memory profile. This
        # never reads GetSecrets and only persists this newly created profile.
        interface.Update2(self._dbus.Dictionary({}, signature='sa{sv}'), self._dbus.UInt32(1),
                          self._dbus.Dictionary({}, signature='sv'), timeout=PROFILE_WRITE_SECONDS)

    def enable_autoconnect(self, connection, settings):
        interface = self._dbus.Interface(self._bus.get_object(NM, connection, introspect=False), NM + '.Settings.Connection')
        committed = {**settings, 'connection': {**settings['connection'], 'autoconnect': True}}
        interface.Update2(self._settings(committed), self._dbus.UInt32(1),
                          self._dbus.Dictionary({}, signature='sv'), timeout=PROFILE_WRITE_SECONDS)

    def commit(self, checkpoint):
        self._manager.CheckpointDestroy(checkpoint, timeout=DBUS_CALL_SECONDS)

    def rollback(self, checkpoint):
        states = self._manager.CheckpointRollback(checkpoint, timeout=DBUS_CALL_SECONDS)
        require(bool(states) and all(int(status) == 0 for status in states.values()))

    def close(self):
        if self._bus is not None:
            self._bus.close()
            self._bus = self._manager = self._device = self._dbus = None


class NetworkManagerBackend:
    """An explicit, one-window coordinator. Test doubles implement D-Bus ops."""
    def __init__(self, operations=None, *, clock=time.monotonic, sleeper=asyncio.sleep):
        self._ops = operations if operations is not None else NetworkManagerDBus()
        self._clock, self._sleep = clock, sleeper
        self._checkpoint = None
        self._begin_task = None
        self._task = None
        self._state = 'idle'
        self._last_diagnostic = None

    @property
    def state(self):
        return self._state

    @property
    def last_diagnostic(self):
        # Local operator diagnostics only: no identifiers, settings, secrets,
        # exception objects, native error strings or protocol payloads.
        return dict(self._last_diagnostic) if self._last_diagnostic is not None else None

    def _elapsed_ms(self, started):
        try:
            return min(DIAGNOSTIC_MAX_MS, max(0, int((self._clock() - started) * 1000)))
        except BaseException:
            # Optional timing must never replace the original failure or stop
            # its rollback, even when an injected clock itself is broken.
            return 0

    async def _diagnose_and_rollback(self, stage, cause, elapsed_ms):
        had_checkpoint = self._checkpoint is not None
        restored = await self._rollback()
        self._last_diagnostic = {
            'stage': stage, 'cause': cause, 'elapsed_ms': elapsed_ms,
            'rollback': 'NOT_AVAILABLE' if not had_checkpoint else 'SUCCESS' if restored else 'FAILED',
        }
        return restored

    async def _wait(self, active, *, ap, budget):
        deadline = self._clock() + budget
        while self._clock() < deadline:
            active_now = await asyncio.to_thread(self._ops.activated, active, ap=ap)
            if self._clock() >= deadline:
                return False
            if active_now:
                return True
            await self._sleep(.25)
        return False

    async def begin(self, ssid, password, *, timeout=180):
        require(self._state == 'idle' and type(timeout) is int and 90 <= timeout <= 300)
        settings = ap_settings(ssid, password)
        self._state = 'preparing'
        async def prepare():
            try:
                # The QR acceptance deadline is separate: a request accepted
                # near its end still needs up to 60s association/settlement.
                self._checkpoint = await asyncio.to_thread(self._ops.checkpoint, timeout + CHECKPOINT_SETTLE_SECONDS)
                require(isinstance(self._checkpoint, str) and self._checkpoint.startswith(ROOT + '/Checkpoint/'))
                _, active = await asyncio.to_thread(self._ops.activate, settings, temporary=True)
                require(await self._wait(active, ap=True, budget=20))
                self._state = 'open'
            except BaseException:
                await self._rollback()
                raise ProvisioningError() from None
        self._begin_task = asyncio.create_task(prepare())
        await asyncio.shield(self._begin_task)

    async def __call__(self, credentials, request_id):
        # Validate again: a programmatic caller cannot bypass the QR parser.
        settings = station_settings(credentials)
        require(self._state == 'open' and self._task is None)
        require(type(request_id) is str and re.fullmatch(r'[A-Za-z0-9_.:-]{1,64}', request_id) is not None)
        self._state = 'connecting'
        async def apply():
            stage, started = 'ACTIVATE', self._clock()
            try:
                connection, active = await asyncio.to_thread(self._ops.activate, settings, temporary=False)
                stage, started = 'VERIFY_ASSOCIATION', self._clock()
                if not await self._wait(active, ap=False, budget=ASSOCIATION_SECONDS):
                    restored = await self._diagnose_and_rollback(stage, 'ASSOCIATION_TIMEOUT', self._elapsed_ms(started))
                    return 'failed' if restored else 'unknown'
                stage, started = 'PERSIST', self._clock()
                await asyncio.to_thread(self._ops.persist, connection)
                # This commits the known local Wi-Fi association, not Internet
                # or cloud reachability. Existing saved profiles are retained.
                stage, started = 'COMMIT', self._clock()
                await asyncio.to_thread(self._ops.commit, self._checkpoint)
                self._checkpoint = None
                # Only the newly accepted/persisted profile may autoconnect on
                # future boots. Failed candidate profiles remain non-auto.
                stage, started = 'AUTOCONNECT', self._clock()
                await asyncio.to_thread(self._ops.enable_autoconnect, connection, settings)
                self._state = 'connected'
                return 'connected'
            except BaseException as error:
                # Save/commit may already have happened. Even if restoring the
                # old link succeeds, do not claim that nothing was persisted.
                # Capture the first failure and its duration BEFORE rollback;
                # a failed/slow rollback must not replace the original stage.
                cause, elapsed_ms = diagnostic_cause(error), self._elapsed_ms(started)
                await self._diagnose_and_rollback(stage, cause, elapsed_ms)
                self._state = 'unknown'
                return 'unknown'
        self._task = asyncio.create_task(apply())
        return await asyncio.shield(self._task)

    async def _rollback(self):
        if self._checkpoint is None:
            self._state = 'failed'
            return True
        try:
            await asyncio.to_thread(self._ops.rollback, self._checkpoint)
            self._checkpoint = None
            self._state = 'failed'
            return True
        except BaseException:
            # The NetworkManager-owned timed checkpoint can still recover;
            # its expiry is not evidence that this caller saw a rollback.
            self._state = 'unknown'
            return False

    async def close(self):
        if self._begin_task is not None:
            try:
                await asyncio.shield(self._begin_task)
            except ProvisioningError:
                pass
        if self._task is not None:
            await asyncio.shield(self._task)
        if self._checkpoint is not None:
            await self._rollback()
        await asyncio.to_thread(self._ops.close)
