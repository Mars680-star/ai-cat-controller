import secrets

import pytest
from fastapi.testclient import TestClient

from ai_cat_controller.local_gateway.app import create_app, allowed_route
from ai_cat_controller.local_gateway.protocol import Card, open_packet, seal_packet
from ai_cat_controller.local_gateway.store import LocalPairingStore
from ai_cat_controller.mini_gateway.app import create_app as create_business_app
from ai_cat_controller.mini_gateway.config import MiniGatewayConfig


@pytest.fixture
def local(tmp_path):
    card = Card('dev_'+'a'*32, 'b'*32, '12'*32)
    store = LocalPairingStore(tmp_path/'local'/'sessions.sqlite3', card=card, serial='original_serial', initialize=True)
    inner = create_business_app(MiniGatewayConfig(db_path=store.path, status_pet_id=store.pet_id), store=store)
    verified = []
    async def verify():
        verified.append(True)
    app = create_app(store=store, gateway=inner, verify_authority=verify)
    with TestClient(app, client=('127.0.0.1', 51111)) as client:
        yield client, card, store, verified


def rpc(local, method, path, body=None, token=None):
    client, card, *_ = local
    request_id = secrets.token_hex(16)
    challenge = client.get('/local/v1/challenge/' + request_id).json()['data']
    packet = seal_packet(card, challenge, request_id,
        dict(method=method, path=path, body=body, accessToken=token), direction='request')
    response = client.post('/local/v1/packet', json=packet)
    assert response.status_code == 200
    return open_packet(card, response.json(), direction='response'), packet


def test_health_does_not_claim_wechat_or_pairing(local):
    client, *_ = local
    response = client.get('/local/health')
    assert response.status_code == 200
    data = response.json()['data']
    assert data['mode'] == 'local-device' and data['wechatConfigured'] is False
    assert 'accessToken' not in response.text and response.headers['cache-control'] == 'no-store'


def test_physical_card_pairs_without_fake_wechat(local):
    client, card, store, verified = local
    pair, _ = rpc(local, 'POST', '/local/pair', {'confirmation':'pair-local-device'})
    assert pair['status'] == 200 and pair['body']['data']['userId'].startswith('loc_')
    assert pair['body']['data']['ttlSeconds'] == 3600 and verified == [True]
    token = pair['body']['data']['accessToken']
    result, _ = rpc(local, 'GET', '/v1/pets', token=token)
    assert result['status'] == 200 and result['body']['data'][0]['petId'] == store.pet_id
    session, _ = rpc(local, 'GET', '/v1/session', token=token)
    assert session['body']['data']['userId'] == store.user_id
    out, _ = rpc(local, 'POST', '/v1/auth/logout', token=token)
    assert out['body']['data']['loggedOut'] is True
    denied, _ = rpc(local, 'GET', '/v1/pets', token=token)
    assert denied['status'] == 401


@pytest.mark.parametrize('path', ['/v1/pets','/v1/auth/wechat','/api/v1/dialog/wake',
    '/docs','/openapi.json','/local/v1/challenge?token=secret'])
def test_no_plain_business_routes_or_query(local, path):
    client, *_ = local
    assert client.get(path).status_code >= 400


def test_replay_or_tampering_never_issues_second_session(local):
    client, card, store, verified = local
    paired, packet = rpc(local, 'POST', '/local/pair', {'confirmation':'pair-local-device'})
    assert paired['status'] == 200
    assert client.post('/local/v1/packet', json=packet).status_code == 403
    assert verified == [True]
    request_id = secrets.token_hex(16)
    challenge = client.get('/local/v1/challenge/' + request_id).json()['data']
    wrong = seal_packet(card, challenge, request_id, {}, direction='request')
    wrong['ciphertext'] = '00' * (len(wrong['ciphertext']) // 2)
    assert client.post('/local/v1/packet', json=wrong).status_code == 403
    assert verified == [True]


@pytest.mark.parametrize('method,path', [('POST','/v1/auth/wechat'), ('GET','/api/v1/pets'),
    ('POST','/v1/pets/test/lifecycle/commands'), ('POST','/v1/pets/test/unbind'),
    ('GET','http://169.254.169.254'), ('GET','/v1/pets?token=abc'), ('GET','/v1/%70ets')])
def test_forbidden_rpc_routes_are_never_forwarded(local, method, path):
    assert not allowed_route(method, path)
    response, _ = rpc(local, method, path, token='a'*43)
    assert response['status'] == 503


def test_bad_private_authority_cannot_pair(tmp_path):
    card = Card('dev_'+'a'*32, 'b'*32, '12'*32)
    store = LocalPairingStore(tmp_path/'local'/'sessions.sqlite3', card=card, serial='original_serial', initialize=True)
    inner = create_business_app(MiniGatewayConfig(db_path=store.path), store=store)
    async def denied():
        raise ValueError('no longer owner')
    app = create_app(store=store, gateway=inner, verify_authority=denied)
    with TestClient(app, client=('127.0.0.1', 51111)) as client:
        result, _ = rpc((client,card,store,None), 'POST','/local/pair', {'confirmation':'pair-local-device'})
        assert result['status'] == 503


def test_revoked_owner_cannot_keep_using_static_local_identity(tmp_path):
    card = Card('dev_'+'a'*32, 'b'*32, '12'*32)
    store = LocalPairingStore(tmp_path/'local'/'sessions.sqlite3', card=card, serial='original_serial', initialize=True)
    inner = create_business_app(MiniGatewayConfig(db_path=store.path), store=store)
    active = [True]
    async def verify():
        if not active[0]:
            raise ValueError('revoked')
    app = create_app(store=store, gateway=inner, verify_authority=verify)
    with TestClient(app, client=('127.0.0.1', 51111)) as client:
        local = (client,card,store,None)
        result, _ = rpc(local,'POST','/local/pair',{'confirmation':'pair-local-device'})
        token = result['body']['data']['accessToken']
        active[0] = False
        for path in ('/v1/session','/v1/pets'):
            result,_ = rpc(local,'GET',path,token=token)
            assert result['status'] == 503 and result['body']['success'] is False


def test_busy_guard_blocks_prepare_without_network_or_new_record(tmp_path):
    card=Card('dev_'+'a'*32,'b'*32,'12'*32)
    store=LocalPairingStore(tmp_path/'local'/'sessions.sqlite3',card=card,serial='synthetic',initialize=True)
    inner=create_business_app(MiniGatewayConfig(db_path=store.path),store=store)
    async def verify(): pass
    async def busy(): raise ValueError('busy')
    class Coordinator:
        async def prepare(self,*args,**kwargs): raise AssertionError('no preparation when busy')
        async def start(self,*args,**kwargs): raise AssertionError('no network when busy')
        async def aclose(self): pass
    app=create_app(store=store,gateway=inner,verify_authority=verify,provisioning=Coordinator(),verify_network_idle=busy)
    with TestClient(app,client=('127.0.0.1',51111)) as client:
        local=(client,card,store,None)
        result,_=rpc(local,'POST','/local/pair',{'confirmation':'pair-local-device'})
        token=result['body']['data']['accessToken']
        result,_=rpc(local,'POST','/local/provision/prepare',
            {'requestId':'local_wifi_'+'c'*32,'confirmation':'open-local-provisioning'},token)
        assert result['status']==503
        result,_ = rpc(local,'POST','/v1/auth/logout',token=token)
        assert result['status'] == 200
