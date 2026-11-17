"""Synthetic real-app owner issuance and local authority, no real device/network."""
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest

from ai_cat_controller.core.config import Settings
from ai_cat_controller.main import create_app

KEY='synthetic-private-device-key'
SERIAL='K1-LOCAL-DELEGATION-001'


def configured(settings,tmp_path):
    serial_path=tmp_path/'serial'; serial_path.write_text(SERIAL)
    return Settings.model_validate({**settings.model_dump(), 'api_key_enabled':True,'api_key':SecretStr(KEY),
        'enable_local_gateway_delegation':True,
        'local_gateway_delegation_path':tmp_path/'local-private'/'delegation.sqlite3',
        'device_serial_path':serial_path})


def client_for(settings,peer='127.0.0.1'):
    return TestClient(create_app(settings),headers={'X-API-Key':KEY},client=(peer,31000))


def login(client,code='synthetic-original-owner'):
    response=client.post('/api/v1/auth/mock-login',json={'login_code':code,'nickname':'合成测试用户'})
    assert response.status_code==200
    return response.json()['data']['session_token']


def prepare(client):
    session=login(client)
    response=client.post('/api/v1/pets/bind',headers={'X-Mock-Session':session},
        json={'device_serial':SERIAL,'pet_name':'合成本机猫','network_name':'Synthetic'})
    assert response.status_code==200
    return session,response.json()['data']['pet']['pet_id']


def issue(client,session,pet,**patch):
    return client.post('/api/v1/local-gateway/delegations',headers={'X-Mock-Session':session},
        json={'pet_id':pet,'device_serial':SERIAL,'confirmation':'install-local-gateway',**patch})


def test_explicit_real_owner_issue_survives_main_restart_without_web_session(settings,tmp_path):
    config=configured(settings,tmp_path)
    with client_for(config) as client:
        session,pet=prepare(client)
        response=issue(client,session,pet)
        assert response.status_code==200 and response.headers['cache-control']=='no-store'
        data=response.json()['data']; token=data['delegation_token']
        assert data['pet_id']==pet and data['device_serial']==SERIAL
        rows=client.get('/api/v1/pets',headers={'X-Local-Gateway':token})
        assert rows.status_code==200 and [p['pet_id'] for p in rows.json()['data']]==[pet]
    with client_for(config) as client:
        assert client.get('/api/v1/pets',headers={'X-Mock-Session':session}).status_code==401
        assert client.get('/api/v1/pets',headers={'X-Local-Gateway':token}).status_code==200


def test_feature_default_off_and_disabled_head_does_not_fall_back_to_api_key(settings,tmp_path):
    config=configured(settings,tmp_path).model_copy(update={'enable_local_gateway_delegation':False})
    with client_for(config) as client:
        session,pet=prepare(client)
        assert issue(client,session,pet).status_code==404
        assert client.get('/api/v1/device/status',headers={'X-Local-Gateway':'lgd_'+'a'*43}).status_code==401
        assert client.get('/api/v1/device/status').status_code==200
    assert not config.local_gateway_delegation_path.exists()


def test_nonowner_missing_session_wrong_device_and_confirmation_do_not_issue(settings,tmp_path):
    config=configured(settings,tmp_path)
    with client_for(config) as client:
        session,pet=prepare(client); stranger=login(client,'synthetic-stranger')
        assert issue(client,stranger,pet).status_code==403
        assert issue(client,'invalid',pet).status_code==403
        assert issue(client,session,pet,device_serial='K1-WRONG-DEVICE').status_code==403
        assert issue(client,session,pet,confirmation='yes').status_code==422
        with sqlite3.connect(config.local_gateway_delegation_path) as db:
            assert db.execute('SELECT COUNT(*) FROM lg_delegation').fetchone()==(0,)


def test_remote_peer_and_forwarded_loopback_never_issue_or_resolve(settings,tmp_path):
    config=configured(settings,tmp_path)
    with client_for(config) as client:
        session,pet=prepare(client)
        token=issue(client,session,pet).json()['data']['delegation_token']
    with client_for(config,'192.168.1.55') as client:
        session=login(client)
        assert issue(client,session,pet).status_code==403
        response=client.post('/api/v1/dialog/wake',json={},
            headers={'X-Local-Gateway':token,'X-Forwarded-For':'127.0.0.1'})
        assert response.status_code==401


@pytest.mark.parametrize('path',['/api/v1/pets/bind','/api/v1/pets/{pet}/unbind',
    '/api/v1/pets/{pet}/memory/clear','/api/v1/product/reset','/api/v1/motion/head/nod',
    '/api/v1/local-gateway/delegations'])
def test_grant_cannot_reach_dangerous_or_issuance_paths(settings,tmp_path,path):
    config=configured(settings,tmp_path)
    with client_for(config) as client:
        session,pet=prepare(client); token=issue(client,session,pet).json()['data']['delegation_token']
        response=client.post(path.format(pet=pet),headers={'X-Local-Gateway':token},json={})
        assert response.status_code in (401,404)


def test_dual_auth_duplicate_grant_and_query_fail_closed(settings,tmp_path):
    config=configured(settings,tmp_path)
    with client_for(config) as client:
        session,pet=prepare(client); token=issue(client,session,pet).json()['data']['delegation_token']
        assert client.get('/api/v1/pets',headers={'X-Local-Gateway':token,'X-Mock-Session':session}).status_code==401
        assert client.get('/api/v1/pets',headers=[('X-Local-Gateway',token),('X-Local-Gateway',token)]).status_code==401
        assert client.get('/api/v1/pets?any=true',headers={'X-Local-Gateway':token}).status_code==401


def test_rebinding_same_owner_changes_epoch_and_old_grant_cannot_raw_wake(settings,tmp_path):
    config=configured(settings,tmp_path)
    with client_for(config) as client:
        session,pet=prepare(client); token=issue(client,session,pet).json()['data']['delegation_token']
        with sqlite3.connect(config.data_path) as db:
            db.execute("UPDATE pets SET bound_at='2026-11-02T00:00:01+00:00' WHERE pet_id=?",(pet,))
        assert client.get('/api/v1/pets',headers={'X-Local-Gateway':token}).status_code==401
        assert client.post('/api/v1/dialog/wake',json={},headers={'X-Local-Gateway':token}).status_code==401
        assert client.app.state.services.adapter.dialog_state=='idle'


def test_rotation_invalidates_original_without_rebinding_or_losing_product(settings,tmp_path):
    config=configured(settings,tmp_path)
    with client_for(config) as client:
        session,pet=prepare(client); first=issue(client,session,pet).json()['data']['delegation_token']
        new=issue(client,session,pet).json()['data']['delegation_token']
        assert new!=first
        assert client.get('/api/v1/pets',headers={'X-Local-Gateway':first}).status_code==401
        assert client.get('/api/v1/pets',headers={'X-Local-Gateway':new}).status_code==200
        assert client.get('/api/v1/pets',headers={'X-Mock-Session':session}).json()['data'][0]['pet_id']==pet


def test_valid_delegation_preserves_business_reads_and_safe_mock_controls(settings,tmp_path):
    config=configured(settings,tmp_path)
    with client_for(config) as client:
        session,pet=prepare(client); token=issue(client,session,pet).json()['data']['delegation_token']
        headers={'X-Local-Gateway':token}
        for suffix in ('dashboard','intimacy','growth','actions','dialog-readonly/status'):
            assert client.get('/api/v1/pets/'+pet+'/'+suffix,headers=headers).status_code==200
        assert client.patch('/api/v1/dialog/config',headers=headers,json={'follow_up_seconds':15}).status_code==200
        assert client.post('/api/v1/dialog/wake',headers=headers,json={}).status_code==200
        assert client.post('/api/v1/dialog/interrupt',headers=headers,json={}).status_code==200
        assert client.post('/api/v1/motion/stop',headers=headers,json={}).status_code==200


def test_real_owner_unbind_invalidates_grant_without_automatic_regrant(settings,tmp_path):
    config=configured(settings,tmp_path)
    with client_for(config) as client:
        session,pet=prepare(client); token=issue(client,session,pet).json()['data']['delegation_token']
        result=client.post('/api/v1/pets/'+pet+'/unbind',headers={'X-Mock-Session':session})
        assert result.status_code==200
        assert client.get('/api/v1/pets',headers={'X-Local-Gateway':token}).status_code==401
        assert client.post('/api/v1/dialog/wake',json={},headers={'X-Local-Gateway':token}).status_code==401
        assert issue(client,session,pet).status_code==403


@pytest.mark.parametrize('changes',[{'api_key_enabled':False}, {'local_gateway_delegation_path':None},
    {'local_gateway_delegation_path':'relative.sqlite3'}, {'local_gateway_delegation_path':'/tmp/../delegation.sqlite3'}])
def test_configuration_requires_explicit_private_authenticated_sidecar(settings,tmp_path,changes):
    config=configured(settings,tmp_path)
    with pytest.raises(ValueError): Settings.model_validate({**config.model_dump(),**changes})
