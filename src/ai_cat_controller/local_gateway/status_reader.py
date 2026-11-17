"""Fixed loopback status sampling; no computer bridge and no client URL."""
import asyncio
from datetime import datetime, timezone
import math

import httpx

from ai_cat_controller.mini_gateway.status_reader import _snapshot, _DIALOG_STATES, _IDLE_STATES, StatusUnavailable
from .protocol import parse_json


def quiescent_wlan():
    """Read-only NM evidence; never activate, persist, rollback or remove profiles."""
    import dbus
    bus = dbus.SystemBus(private=True)
    try:
        name = 'org.freedesktop.NetworkManager'
        properties = 'org.freedesktop.DBus.Properties'
        root = bus.get_object(name, '/org/freedesktop/NetworkManager', introspect=False)
        manager = dbus.Interface(root, name)
        props = dbus.Interface(root, properties)
        if props.Get(name, 'Checkpoints', timeout=1):
            return False
        path = manager.GetDeviceByIpIface('wlan0', timeout=1)
        obj = bus.get_object(name, path, introspect=False)
        device = dbus.Interface(obj, properties)
        values = device.GetAll(name + '.Device', timeout=1)
        if not (str(values.get('Interface')) == 'wlan0' and bool(values.get('Managed'))
                and int(values.get('State', -1)) == 100 and str(values.get('ActiveConnection', '/')) != '/'):
            return False
        return int(device.Get(name + '.Device.Wireless', 'Mode', timeout=1)) == 2
    finally:
        bus.close()


class DeviceStatusReader:
    def __init__(self, *, api_key, local_delegation, expected_serial, transport=None):
        self._key, self._delegation, self.expected_serial = api_key, local_delegation, expected_serial
        self._transport = transport

    async def verify_network_idle(self):
        """Read-only guard. Never stops an action or interrupts a conversation."""
        await asyncio.wait_for(self._verify_network_idle(), 3)

    async def verify_network_quiescent(self):
        # Only bounded read calls run in this worker. Timing out a read cannot
        # leave a network-changing background operation behind.
        if await asyncio.wait_for(asyncio.to_thread(quiescent_wlan), 5) is not True:
            raise ValueError('Network review requires a stable ordinary connection')

    async def _verify_network_idle(self):
        async with httpx.AsyncClient(transport=self._transport, trust_env=False,
                follow_redirects=False, timeout=2) as client:
            async def get(path):
                async with client.stream('GET', 'http://127.0.0.1:8000' + path,
                        headers={'X-API-Key': self._key, 'X-Local-Gateway': self._delegation,
                            'Accept':'application/json', 'Accept-Encoding':'identity'}) as response:
                    if response.status_code != 200 or response.headers.get('content-encoding','identity') not in ('','identity'):
                        raise ValueError('Network setup requires an idle device')
                    raw = bytearray()
                    async for part in response.aiter_bytes():
                        raw.extend(part)
                        if len(raw) > 65536:
                            raise ValueError('Network setup requires an idle device')
                value = parse_json(bytes(raw), maximum=65536)
                if value.get('success') is not True or type(value.get('data')) is not dict:
                    raise ValueError('Network setup requires an idle device')
                return value['data']
            device, voice = await asyncio.gather(get('/api/v1/device/status'), get('/api/v1/dialog/status'))
        if not (device.get('device_serial') == self.expected_serial and device.get('adapter_mode') == 'local_k1'
                and device.get('current_action') == 'idle' and voice.get('session_active') is False
                and voice.get('audio_playing') is False and voice.get('stale') is False
                and voice.get('state') in {'offline','ready','waiting_wake','interrupted'}):
            raise ValueError('Network setup requires an idle device')

    async def read(self):
        try:
            return await asyncio.wait_for(self._read(), 3)
        except Exception:
            raise StatusUnavailable('Device status is temporarily unavailable') from None

    async def _read(self):
        async with httpx.AsyncClient(transport=self._transport, trust_env=False, follow_redirects=False, timeout=2) as client:
            async def get(path):
                async with client.stream('GET', 'http://127.0.0.1:8000' + path,
                        headers={'X-API-Key': self._key, 'X-Local-Gateway': self._delegation,
                                 'Accept': 'application/json', 'Accept-Encoding': 'identity'}) as response:
                    if response.status_code != 200 or response.headers.get('content-encoding', 'identity') not in ('', 'identity'):
                        raise ValueError('Status unavailable')
                    raw = bytearray()
                    async for part in response.aiter_bytes():
                        raw.extend(part)
                        if len(raw) > 65536:
                            raise ValueError('Status unavailable')
                value = parse_json(bytes(raw), maximum=65536)
                if value.get('success') is not True or type(value.get('data')) is not dict:
                    raise ValueError('Status unavailable')
                return value['data']
            health, device, voice = await asyncio.gather(get('/health'), get('/api/v1/device/status'), get('/api/v1/dialog/status'))
        if (device.get('device_serial') != self.expected_serial or device.get('adapter_mode') != 'local_k1'
                or health.get('status') != 'ok' or health.get('adapter') != 'local_k1'):
            raise ValueError('Wrong device')
        available = device.get('battery_available')
        if type(available) is not bool:
            raise ValueError('Missing status')
        state = voice.get('state')
        known = isinstance(state, str) and state in _DIALOG_STATES
        state = state if known else 'unavailable'
        now = datetime.now(timezone.utc)
        now_ms = int(now.timestamp() * 1000)
        updated, deadline = voice.get('updated_at_ms'), voice.get('follow_up_deadline_ms')
        if any(type(value) is not int or not 0 <= value <= 10**15 for value in (updated, deadline)):
            raise ValueError('Missing status time')
        voltage = device.get('battery_voltage_mv')
        if voltage is not None and (type(voltage) is not int or not 0 <= voltage <= 20000):
            raise ValueError('Invalid battery voltage')
        data = {'readOnly': True, 'sampledAt': now.isoformat().replace('+00:00', 'Z'),
            'health': {'status': 'ok', 'adapter': 'local_k1', 'version': health.get('version')},
            'device': {'connected': device.get('connected'), 'deviceSerial': self.expected_serial[:3] + '****' + self.expected_serial[-4:],
                'networkName': None, 'lastSeenAt': None,
                'batteryPercent': device.get('battery_percent') if available else None,
                'batteryState': device.get('battery_status') if available else 'unavailable',
                'batteryVoltage': voltage / 1000 if available and voltage is not None else None,
                'chargerOnline': device.get('charger_online') if available else None,
                'batteryPresent': device.get('battery_present') if available else None},
            'dialog': {'state': state,
                'elapsedSeconds': min(86400, max(0, (now_ms-updated)//1000)) if state not in _IDLE_STATES and updated > 0 else None,
                'followUpRemaining': min(120, max(0, math.ceil((deadline-now_ms)/1000))),
                'canInterrupt': voice.get('can_interrupt'), 'sessionActive': voice.get('session_active'),
                'stale': voice.get('stale') if known else True}}
        return _snapshot({'success': True, 'data': data})
