"""True AES/ASGI closeout contracts; all device/NM reads are synthetic.

Only temporary SQLite and private audit files are written. The seeded original
row is synthetic authenticated UNKNOWN evidence, not a real network operation.
Backend/server factories fail immediately if any route tries to create an AP.
"""
import base64
import hashlib
import json
import secrets
import sys
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from ai_cat_controller.local_gateway.app import create_app
from ai_cat_controller.local_gateway.cli import verify_authority
from ai_cat_controller.local_gateway.protocol import Card, open_packet, seal_packet
from ai_cat_controller.local_gateway.provisioning import LocalProvisioningCoordinator
from ai_cat_controller.local_gateway.provision_reviews import ACKNOWLEDGEMENTS
from ai_cat_controller.local_gateway.status_reader import DeviceStatusReader, quiescent_wlan
from ai_cat_controller.local_gateway.store import LocalPairingStore
from ai_cat_controller.mini_gateway.app import create_app as business_app
from ai_cat_controller.mini_gateway.config import MiniGatewayConfig
from ai_cat_controller.provisioning.protocol import Invitation, canonical, encode_packet


DEVICE, CARD_ID, WINDOW = 'dev_' + 'a' * 32, 'b' * 32, 'c' * 32
OUTER, INNER = 'local_wifi_synthetic', 'wifi_synthetic'
OWNER, EPOCH, SERIAL = 'synthetic_product_owner', '2026-11-02T00:00:00Z', 'SYNTHETIC-SERIAL-128'
BASE = '/local/provision/'
STATUS_FIELDS = {'eligible', 'cardId', 'original', 'status', 'resolution', 'reviewId', 'archivedAt', 'acknowledgements'}


class FakeNetworkManager:
    """Allow only fixed-interface reads, never a D-Bus write method."""
    NAME = 'org.freedesktop.NetworkManager'
    PROPS = 'org.freedesktop.DBus.Properties'
    ROOT = '/org/freedesktop/NetworkManager'
    WLAN = ROOT + '/Devices/7'

    def __init__(self, state=None):
        self.state = state if state is not None else {}
        self.calls, self.closed = [], 0

    def SystemBus(self, *, private):
        assert private is True
        self.calls.append(('SystemBus',))
        return self

    def get_object(self, name, path, *, introspect):
        assert name == self.NAME and path in {self.ROOT, self.WLAN} and introspect is False
        self.calls.append(('get_object', path))
        return path

    def Interface(self, obj, interface):
        assert obj in {self.ROOT, self.WLAN} and interface in {self.NAME, self.PROPS}
        manager = self

        class ReadProxy:
            def GetDeviceByIpIface(self, name, *, timeout):
                assert obj == manager.ROOT and interface == manager.NAME and name == 'wlan0' and timeout == 1
                manager.calls.append(('GetDeviceByIpIface', name, timeout))
                if manager.state.get('nm_timeout') == 'lookup':
                    raise TimeoutError('synthetic read timeout')
                return manager.WLAN

            def GetAll(self, name, *, timeout):
                assert obj == manager.WLAN and interface == manager.PROPS and name == manager.NAME + '.Device' and timeout == 1
                manager.calls.append(('GetAll', name, timeout))
                if manager.state.get('nm_timeout') == 'device':
                    raise TimeoutError('synthetic read timeout')
                return {'Interface': manager.state.get('interface', 'wlan0'), 'Managed': manager.state.get('managed', True),
                        'State': manager.state.get('device_state', 100), 'ActiveConnection': manager.state.get('active', '/active/1')}

            def Get(self, name, prop, *, timeout):
                assert interface == manager.PROPS and timeout == 1
                manager.calls.append(('Get', name, prop, timeout))
                if obj == manager.ROOT:
                    assert name == manager.NAME and prop == 'Checkpoints'
                    if manager.state.get('nm_timeout') == 'checkpoints':
                        raise TimeoutError('synthetic read timeout')
                    return manager.state.get('checkpoints', [])
                assert obj == manager.WLAN and name == manager.NAME + '.Device.Wireless' and prop == 'Mode'
                if manager.state.get('revoke_on_mode'):
                    manager.state['owner'] = 'revoked_owner'
                if manager.state.get('nm_timeout') == 'mode':
                    raise TimeoutError('synthetic read timeout')
                return manager.state.get('mode', 2)

            def __getattr__(self, name):
                raise AssertionError('No Activate/Update/checkpoint mutation is allowed: ' + name)

        return ReadProxy()

    def close(self):
        self.closed += 1


@pytest.fixture
def closeout(tmp_path, monkeypatch):
    card = Card(DEVICE, CARD_ID, '12' * 32)
    store = LocalPairingStore(tmp_path / 'sessions' / 'sessions.sqlite3', card=card, serial=SERIAL, initialize=True)
    state = {'owner': OWNER, 'epoch': EPOCH}
    nm = FakeNetworkManager(state)
    monkeypatch.setitem(sys.modules, 'dbus', nm)
    http_calls, network_calls = [], []

    class NoNetworkBackend:
        def __init__(self):
            network_calls.append('backend-created')
            raise AssertionError('closeout must never create a real or simulated AP')

    class NoNetworkServer:
        def __init__(self, *args):
            network_calls.append('server-created')
            raise AssertionError('closeout must never start a provisioning socket')

    clock = SimpleNamespace(now=1000.0)
    coordinator = LocalProvisioningCoordinator(db_path=tmp_path / 'provision' / 'provisioning.sqlite3', device_id=DEVICE,
        backend_factory=NoNetworkBackend, server_factory=NoNetworkServer, clock=lambda: clock.now, monotonic=lambda: clock.now,
        review_identity={'cardId': CARD_ID, 'userId': OWNER, 'bindingEpoch': EPOCH})
    invite = Invitation(DEVICE, WINDOW, '34' * 32)
    packet = encode_packet(invite, INNER, 'configure', {'status': 'unknown'}, direction='response', nonce=b'\x01' + b'\x07' * 11)
    receipt = 'AICAT-PROVISION-RECEIPT:1:' + base64.urlsafe_b64encode(canonical(packet)).decode().rstrip('=')
    with coordinator._connect() as connection:
        connection.execute('INSERT INTO lp_requests VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (OUTER, DEVICE, WINDOW, invite.encode(), 'unknown', 'AI-CAT-ABC123', 600.0, 780.0, INNER, receipt, 800.0))
    original = coordinator._row(OUTER)
    original_bytes = coordinator.path.read_bytes()
    config = SimpleNamespace(api_key='synthetic-api-key', local_delegation='synthetic-local-grant',
                             product_pet_id='synthetic_product_pet', owner_user_id=OWNER, device_serial=SERIAL, binding_epoch=EPOCH)

    def read_device(request):
        http_calls.append(request)
        assert request.method == 'GET' and request.url.host == '127.0.0.1' and request.url.port == 8000
        assert request.headers['X-API-Key'] == config.api_key and request.headers['X-Local-Gateway'] == config.local_delegation
        if request.url.path == '/api/v1/pets':
            data = [{'pet_id': config.product_pet_id, 'owner_user_id': state['owner'], 'serial_number': SERIAL, 'bound_at': state['epoch']}]
        elif request.url.path == '/api/v1/device/status':
            data = {'device_serial': SERIAL, 'adapter_mode': 'local_k1', 'current_action': state.get('motion', 'idle')}
        else:
            assert request.url.path == '/api/v1/dialog/status'
            data = {'session_active': state.get('voice_active', False), 'audio_playing': False, 'stale': False, 'state': 'waiting_wake'}
        return httpx.Response(200, json={'success': True, 'data': data})

    transport = httpx.MockTransport(read_device)
    reader = DeviceStatusReader(api_key=config.api_key, local_delegation=config.local_delegation, expected_serial=SERIAL, transport=transport)

    async def authorized():
        await verify_authority(config, transport=transport)

    gateway = business_app(MiniGatewayConfig(db_path=store.path, status_pet_id=store.pet_id), store=store)
    app = create_app(store=store, gateway=gateway, provisioning=coordinator, verify_authority=authorized,
                     verify_network_idle=reader.verify_network_idle, verify_network_quiescent=reader.verify_network_quiescent)
    with TestClient(app, client=('127.0.0.1', 51123)) as client:
        h = SimpleNamespace(client=client, card=card, store=store, coordinator=coordinator, state=state, nm=nm,
                            original=original, original_bytes=original_bytes, receipt=receipt, invite=invite, clock=clock,
                            http_calls=http_calls, network_calls=network_calls, nonces=[])
        h.token = rpc(h, '/local/pair', {'confirmation': 'pair-local-device'}, authenticated=False)['body']['data']['accessToken']
        yield h
        assert network_calls == []


def rpc(h, path, body, *, authenticated=True, tamper_card=False, drop_response=False):
    request_id = secrets.token_hex(16)
    challenge_response = h.client.get('/local/v1/challenge/' + request_id)
    assert challenge_response.status_code == 200
    challenge = challenge_response.json()['data']
    packet = seal_packet(h.card, challenge, request_id, {'method': 'POST', 'path': path, 'body': body,
        'accessToken': h.token if authenticated else None}, direction='request')
    h.nonces.append(packet['nonce'])
    if tamper_card:
        packet['cardId'] = 'd' * 32
    response = h.client.post('/local/v1/packet', json=packet)
    if tamper_card:
        assert response.status_code == 403
        return response
    assert response.status_code == 200
    assert 'reviewToken' not in response.text and 'operator_archived' not in response.text
    if drop_response:
        return None  # Model a lost reply before any client-side decryption.
    return open_packet(h.card, response.json(), direction='response')


def prepare(h, **changes):
    body = {'requestId': OUTER, 'windowId': WINDOW, 'networkRequestId': INNER, 'acknowledgements': list(ACKNOWLEDGEMENTS)}
    body.update(changes)
    return rpc(h, BASE + 'archive-prepare', body)


def confirm(h, prepared, **changes):
    body = {'requestId': OUTER, 'windowId': WINDOW, 'networkRequestId': INNER, 'reviewToken': prepared['reviewToken'],
            'acknowledgements': list(ACKNOWLEDGEMENTS), 'confirmation': 'archive-unknown-provisioning'}
    body.update(changes)
    return rpc(h, BASE + 'archive-confirm', body)


def assert_untouched(h):
    assert h.coordinator._row(OUTER) == h.original
    assert h.coordinator.path.read_bytes() == h.original_bytes
    assert h.coordinator._runner is None and h.coordinator._contexts == {} and h.network_calls == []


@pytest.mark.parametrize('suffix', ['archive-status', 'archive-prepare', 'archive-confirm'])
def test_closeout_is_encrypted_only_and_login_required(closeout, suffix):
    h = closeout
    assert h.client.post(BASE + suffix, json={'requestId': OUTER}).status_code == 400
    result = rpc(h, BASE + suffix, {'requestId': OUTER}, authenticated=False)
    assert result['status'] == 503 and result['body']['success'] is False
    assert not (h.coordinator.path.parent / 'provision-reviews').exists()
    assert_untouched(h)


def test_closeout_rejects_wrong_card_before_business_dispatch(closeout):
    h = closeout
    before = len(h.http_calls)
    rpc(h, BASE + 'archive-status', {'requestId': OUTER}, tamper_card=True)
    assert len(h.http_calls) == before
    assert_untouched(h)


@pytest.mark.parametrize('changes', [
    {'requestId': 'other'}, {'windowId': 'd' * 32}, {'networkRequestId': 'other'}, {'windowId': [WINDOW]},
    {'acknowledgements': []}, {'acknowledgements': ['window-ended', 'usb-checked', 'accept-unknown']},
    {'userId': 'other_owner'}, {'cardId': 'd' * 32}, {'acknowledgements': [True] * 4},
])
def test_closeout_prepare_rejects_other_identity_or_missing_explicit_acknowledgements(closeout, changes):
    h = closeout
    assert prepare(h, **changes)['status'] == 503
    assert not (h.coordinator.path.parent / 'provision-reviews').exists()
    assert_untouched(h)


@pytest.mark.parametrize('changes', [
    {'requestId': 'other'}, {'windowId': 'd' * 32}, {'networkRequestId': 'other'}, {'reviewToken': 'forged'},
    {'acknowledgements': []}, {'confirmation': 'open-local-provisioning'}, {'userId': 'other_owner'}, {'cardId': 'd' * 32},
])
def test_closeout_confirm_rechecks_original_tuple_token_and_body(closeout, changes):
    h = closeout
    prepared = prepare(h)['body']['data']
    assert confirm(h, prepared, **changes)['status'] == 503
    assert not (h.coordinator.path.parent / 'provision-reviews').exists()
    assert_untouched(h)


@pytest.mark.parametrize('field,value', [('owner', 'revoked_owner'), ('epoch', '2026-11-03T00:00:00Z')])
@pytest.mark.parametrize('stage', ['archive-status', 'archive-prepare', 'archive-confirm'])
def test_closeout_fresh_authority_blocks_revoked_owner_or_binding(closeout, field, value, stage):
    h = closeout
    prepared = prepare(h)['body']['data'] if stage == 'archive-confirm' else None
    h.state[field] = value
    result = (confirm(h, prepared) if prepared else prepare(h) if stage == 'archive-prepare'
              else rpc(h, BASE + stage, {'requestId': OUTER}))
    assert result['status'] == 503 and result['body']['success'] is False
    assert not (h.coordinator.path.parent / 'provision-reviews').exists()
    assert_untouched(h)


@pytest.mark.parametrize('change', [{'motion': 'head_nod'}, {'voice_active': True}, {'checkpoints': ['/checkpoint/1']}, {'mode': 3}])
def test_busy_checkpoint_and_ap_reads_hide_eligibility_and_deny_confirm(closeout, change):
    h = closeout
    h.state.update(change)
    status = rpc(h, BASE + 'archive-status', {'requestId': OUTER})
    assert status['status'] == 200 and status['body']['data']['eligible'] is False
    prepared = prepare(h)['body']['data']
    assert confirm(h, prepared)['status'] == 503
    assert not (h.coordinator.path.parent / 'provision-reviews').exists()
    assert_untouched(h)


def test_owner_revocation_during_nm_read_is_rechecked_immediately_before_append(closeout):
    h = closeout
    prepared = prepare(h)['body']['data']
    h.state['revoke_on_mode'] = True
    assert confirm(h, prepared)['status'] == 503
    assert not (h.coordinator.path.parent / 'provision-reviews').exists()
    assert_untouched(h)


def test_success_contract_appends_owner_card_audit_without_changing_original_row_receipt_or_starting_ap(closeout):
    h = closeout
    status = rpc(h, BASE + 'archive-status', {'requestId': OUTER})['body']['data']
    assert set(status) == STATUS_FIELDS and status['eligible'] is True
    assert status['resolution'] == 'pending' and status['reviewId'] is None and status['acknowledgements'] == []
    prepared = prepare(h)['body']['data']
    assert set(prepared) == {'reviewToken', 'expiresAt', 'reviewId', 'cardId', 'original'}
    assert prepared['expiresAt'] == h.clock.now + 30 and prepared['cardId'] == CARD_ID
    result = confirm(h, prepared)
    assert result['status'] == 200
    audit = result['body']['data']
    assert set(audit) == STATUS_FIELDS and audit['eligible'] is False and audit['status'] == 'unknown'
    assert audit['resolution'] == 'operator_archived' and audit['reviewId'] == prepared['reviewId']
    assert audit['archivedAt'] == int(h.clock.now * 1000) and audit['acknowledgements'] == ACKNOWLEDGEMENTS
    assert audit['original'] == {'deviceId': DEVICE, 'requestId': OUTER, 'windowId': WINDOW, 'networkRequestId': INNER}
    files = list((h.coordinator.path.parent / 'provision-reviews').iterdir())
    assert len(files) == 1
    stored = json.loads(files[0].read_bytes())
    assert stored['cardId'] == CARD_ID and stored['userId'] == OWNER and stored['bindingEpoch'] == EPOCH
    assert stored['receiptSha256'] == hashlib.sha256(h.receipt.encode('ascii')).hexdigest()
    assert h.receipt not in files[0].read_text() and prepared['reviewToken'] not in files[0].read_text()
    resume = rpc(h, BASE + 'resume', {'requestId': OUTER})['body']['data']
    assert resume['invitation'] == h.invite.encode() and resume['receiptQr'] == h.receipt and resume['status'] == 'unknown'
    assert_untouched(h)


def test_lost_closeout_response_recovers_only_same_id_read_without_new_audit_or_nonce_reuse(closeout):
    h = closeout
    prepared = prepare(h)['body']['data']
    rpc(h, BASE + 'archive-confirm', {'requestId': OUTER, 'windowId': WINDOW, 'networkRequestId': INNER,
        'reviewToken': prepared['reviewToken'], 'acknowledgements': list(ACKNOWLEDGEMENTS),
        'confirmation': 'archive-unknown-provisioning'}, drop_response=True)
    files = list((h.coordinator.path.parent / 'provision-reviews').iterdir())
    before = {p.name: p.read_bytes() for p in files}
    first = rpc(h, BASE + 'archive-status', {'requestId': OUTER})
    second = rpc(h, BASE + 'archive-status', {'requestId': OUTER})
    assert first == second and first['body']['data']['reviewId'] == prepared['reviewId']
    assert {p.name: p.read_bytes() for p in files} == before
    assert rpc(h, BASE + 'archive-status', {'requestId': 'different-original'})['status'] == 503
    assert len(h.nonces) == len(set(h.nonces))
    assert_untouched(h)


def test_api_deadline_plus_ninety_seconds_and_thirty_second_confirmation_are_both_enforced(closeout):
    h = closeout
    h.clock.now = h.original['deadline'] + 90 - 0.001
    assert rpc(h, BASE + 'archive-status', {'requestId': OUTER})['body']['data']['eligible'] is False
    assert prepare(h)['status'] == 503
    h.clock.now = h.original['deadline'] + 90
    prepared = prepare(h)['body']['data']
    h.clock.now += 30
    assert confirm(h, prepared)['status'] == 503
    assert not (h.coordinator.path.parent / 'provision-reviews').exists()
    assert_untouched(h)


def test_even_idempotent_confirm_rechecks_revocation_instead_of_bypassing_guard_with_existing_audit(closeout):
    h = closeout
    prepared = prepare(h)['body']['data']
    assert confirm(h, prepared)['status'] == 200
    h.state['owner'] = 'revoked_owner'
    assert confirm(h, prepared)['status'] == 503
    assert_untouched(h)


@pytest.mark.parametrize('change,expected', [
    ({}, True), ({'checkpoints': ['/checkpoint/1']}, False), ({'device_state': 80}, False),
    ({'managed': False}, False), ({'interface': 'eth0'}, False), ({'active': '/'}, False),
    ({'mode': 3}, False), ({'mode': 1}, False),
])
def test_quiescent_wlan_reads_fixed_interface_state100_infra2_without_mutations(monkeypatch, change, expected):
    nm = FakeNetworkManager(change)
    monkeypatch.setitem(sys.modules, 'dbus', nm)
    assert quiescent_wlan() is expected
    assert nm.closed == 1
    assert all(call[0] in {'SystemBus', 'get_object', 'GetDeviceByIpIface', 'GetAll', 'Get'} for call in nm.calls)


@pytest.mark.parametrize('stage', ['checkpoints', 'lookup', 'device', 'mode'])
def test_quiescent_wlan_passes_per_read_timeout_and_closes_private_bus_on_timeout(monkeypatch, stage):
    nm = FakeNetworkManager({'nm_timeout': stage})
    monkeypatch.setitem(sys.modules, 'dbus', nm)
    with pytest.raises(TimeoutError):
        quiescent_wlan()
    assert nm.closed == 1


@pytest.mark.asyncio
async def test_quiescent_guard_has_five_second_outer_timeout_without_starting_network_work(monkeypatch):
    import ai_cat_controller.local_gateway.status_reader as module
    calls = []

    def forbidden_real_dbus():
        calls.append('unexpected-worker')
        raise AssertionError('timeout test must not start even a fake worker')

    async def timed_out(awaitable, timeout):
        assert timeout == 5
        awaitable.close()
        raise TimeoutError('synthetic outer read deadline')

    reader = DeviceStatusReader(api_key='synthetic', local_delegation='synthetic', expected_serial=SERIAL)
    with monkeypatch.context() as patch:
        patch.setattr(module, 'quiescent_wlan', forbidden_real_dbus)
        patch.setattr(module.asyncio, 'wait_for', timed_out)
        with pytest.raises(TimeoutError):
            await reader.verify_network_quiescent()
    assert calls == []
