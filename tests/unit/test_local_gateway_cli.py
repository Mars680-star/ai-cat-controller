"""Private synthetic configuration; no real credential, network or daemon use."""
import asyncio
import json
from pathlib import Path
import sqlite3

import httpx
import pytest

from ai_cat_controller.local_gateway.cli import main,build_app,verify_authority,close_business_resources
from ai_cat_controller.local_gateway.configuration import load_configuration,check_existing_state
from ai_cat_controller.local_gateway.protocol import Card
from ai_cat_controller.local_gateway.store import LocalPairingStore
from ai_cat_controller.local_gateway.status_reader import DeviceStatusReader


@pytest.fixture
def private_config(tmp_path):
    state=tmp_path/'state'; state.mkdir(mode=0o700)
    card=Card('dev_'+'a'*32,'b'*32,'12'*32)
    store=LocalPairingStore(state/'sessions.sqlite3',card=card,serial='K1-SYNTHETIC-001',initialize=True)
    store.close()
    value=dict(v=1,device_serial='K1-SYNTHETIC-001',device_id=card.device_id,card_id=card.card_id,
        card_key=card.key_hex,api_key='synthetic-device-secret',local_delegation='lgd_'+'a'*43,
        product_pet_id='pet_existing',owner_user_id='user_existing',binding_epoch='2026-11-02T00:00:00+00:00',
        state_dir=str(state),enabled={key:False for key in ('motion','interactions','dialog','preferences','feedback')})
    path=tmp_path/'config.json'; path.write_text(json.dumps(value)); path.chmod(0o600)
    return path,value


def rewrite(path,value):
    path.write_text(json.dumps(value))


def snapshot(directory):
    return {p.name:(p.read_bytes(),p.stat().st_mtime_ns) for p in directory.iterdir() if p.is_file()}


def test_readonly_check_does_not_start_service_network_or_repair_state(private_config,monkeypatch,capsys):
    path,value=private_config; state=Path(value['state_dir']); before=snapshot(state)
    def forbidden(*args,**kwargs): raise AssertionError('No service or client construction in --check')
    monkeypatch.setattr('ai_cat_controller.local_gateway.cli.build_app',forbidden)
    monkeypatch.setattr(httpx,'AsyncClient',forbidden)
    assert main(['--config-file',str(path),'--check'])==0
    assert snapshot(state)==before
    output=capsys.readouterr().out
    assert 'network=0' in output and value['api_key'] not in output and value['card_key'] not in output


def test_secret_fields_omitted_from_config_repr(private_config):
    path,value=private_config; config=load_configuration(path)
    for key in ('card_key','api_key','local_delegation'): assert value[key] not in repr(config)
    assert config.discovery is False and check_existing_state(config)==1


@pytest.mark.parametrize('field,bad',[('v',True),('state_dir','relative'),('device_serial','bad serial'),
    ('card_key','ff'*31),('card_id','F'*32),('api_key','contains\nnewline'),
    ('local_delegation','ordinary_web_session'),('owner_user_id','bad owner'),
    ('product_pet_id',''),('binding_epoch','2026-11-02T00:00:00'),('discovery',1),
    ('upstream_url','http://attacker.invalid'),('port',9999),('enabled',{'motion':True})])
def test_invalid_config_is_secret_safe_and_has_no_state_side_effect(private_config,field,bad,capsys):
    path,value=private_config; before=snapshot(Path(value['state_dir']))
    rewrite(path,{**value,field:bad})
    with pytest.raises(SystemExit) as error: main(['--config-file',str(path),'--check'])
    assert error.value.code==2 and snapshot(Path(value['state_dir']))==before
    text=capsys.readouterr().err
    assert value['api_key'] not in text and value['local_delegation'] not in text and value['card_key'] not in text


def test_duplicates_symlinks_permissions_and_missing_counter_fail_without_repair(private_config,tmp_path):
    path,value=private_config
    path.write_text(json.dumps(value)[:-1]+',"v":1}')
    with pytest.raises(ValueError): load_configuration(path)
    rewrite(path,value); path.chmod(0o644)
    with pytest.raises(ValueError): load_configuration(path)
    path.chmod(0o600); link=tmp_path/'link.json'; link.symlink_to(path)
    with pytest.raises(ValueError): load_configuration(link)
    config=load_configuration(path)
    with sqlite3.connect(config.sessions_path) as db: db.execute('DELETE FROM lg_counter')
    before=snapshot(config.state_dir)
    with pytest.raises(ValueError): check_existing_state(config)
    assert snapshot(config.state_dir)==before


def test_foreign_existing_business_database_unchanged(private_config):
    path,value=private_config; config=load_configuration(path)
    foreign=config.state_dir/'motion.sqlite3'
    with sqlite3.connect(foreign) as db: db.execute('CREATE TABLE pets(id TEXT)')
    foreign.chmod(0o600); before=snapshot(config.state_dir)
    with pytest.raises(ValueError): check_existing_state(config)
    assert snapshot(config.state_dir)==before


@pytest.mark.parametrize('argv',[[],['--config-file','relative','--check'],
    ['--config-file','/tmp/no-file','--host','0.0.0.0'],['--config-file','/tmp/no-file','--port','9999']])
def test_no_unbounded_address_or_implicit_config(argv):
    with pytest.raises(SystemExit): main(argv)


@pytest.mark.asyncio
async def test_authority_uses_literal_loopback_and_offline_existing_owner_can_pair(private_config):
    path,value=private_config; config=load_configuration(path); seen=[]
    async def handler(request):
        seen.append(request)
        assert str(request.url)=='http://127.0.0.1:8000/api/v1/pets'
        assert request.headers['X-Local-Gateway']==value['local_delegation']
        assert request.headers['X-API-Key']==value['api_key'] and 'X-Mock-Session' not in request.headers
        return httpx.Response(200,json={'success':True,'data':[{'pet_id':value['product_pet_id'],
            'owner_user_id':value['owner_user_id'],'serial_number':value['device_serial'],
            'bound_at':value['binding_epoch'],'online':False}]})
    await verify_authority(config,transport=httpx.MockTransport(handler))
    assert len(seen)==1


@pytest.mark.asyncio
@pytest.mark.parametrize('patch',[{'pet_id':'other'},{'owner_user_id':'other'},
    {'serial_number':'other'},{'bound_at':'2026-11-02T00:00:01+00:00'}])
async def test_authority_never_adopts_replacement_binding(private_config,patch):
    path,value=private_config; config=load_configuration(path)
    pet={'pet_id':value['product_pet_id'],'owner_user_id':value['owner_user_id'],
        'serial_number':value['device_serial'],'bound_at':value['binding_epoch'],**patch}
    async def handler(request): return httpx.Response(200,json={'success':True,'data':[pet]})
    with pytest.raises(ValueError): await verify_authority(config,transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_shutdown_does_not_cancel_or_close_unsettled_business_requests():
    event=asyncio.Event(); task=asyncio.create_task(event.wait()); calls=[]
    class Resource:
        _tasks={'one':task}; _stop_tasks=set()
        def close(self): calls.append('resource')
    class Reader:
        def close(self): calls.append('reader')
    try:
        with pytest.raises(RuntimeError): await close_business_resources([Resource()],Reader(),timeout=.001)
        assert not task.done() and calls==[]
        event.set(); await task
        await close_business_resources([Resource()],Reader())
        assert calls==['resource','reader']
    finally:
        if not task.done(): task.cancel()


def test_normal_cli_bindings_are_fixed_without_starting_server(private_config,monkeypatch):
    path,_=private_config; seen=[]
    monkeypatch.setattr('ai_cat_controller.local_gateway.cli.build_app',lambda config: 'synthetic-app')
    monkeypatch.setattr('ai_cat_controller.local_gateway.cli.uvicorn.run',lambda app,**kw:seen.append((app,kw)))
    assert main(['--config-file',str(path)])==0
    assert seen[0][0]=='synthetic-app'
    kw=seen[0][1]
    assert kw['host']=='0.0.0.0' and kw['port']==8789 and kw['limit_concurrency']<=8
    assert kw['proxy_headers'] is False and kw['access_log'] is False


def test_real_factory_and_health_are_dormant_and_close_all_locks(private_config,monkeypatch):
    from fastapi.testclient import TestClient
    path,_=private_config; config=load_configuration(path)
    def forbidden(*args,**kwargs): raise AssertionError('No network in build/start/health')
    monkeypatch.setattr(httpx,'AsyncClient',forbidden)
    app=build_app(config)
    with TestClient(app,client=('127.0.0.1',51234)) as client:
        response=client.get('/local/health')
        assert response.status_code==200 and response.json()['data']['wechatConfigured'] is False
        assert client.get('/v1/pets').status_code>=400
    assert check_existing_state(config)==6
    reopened=LocalPairingStore(config.sessions_path,card=config.card,serial=config.device_serial)
    reopened.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('patch', [None, {'current_action':'head_nod'}, {'device_serial':'wrong'},
    {'adapter_mode':'mock'}, {'session_active':True}, {'audio_playing':True}, {'stale':True},
    {'state':'thinking'}, {'state':'listening'}, {'audio_playing':None}])
async def test_network_idle_guard_is_fixed_readonly_and_does_not_interrupt(patch):
    device=dict(device_serial='synthetic',adapter_mode='local_k1',current_action='idle')
    voice=dict(session_active=False,audio_playing=False,stale=False,state='offline')
    if patch:
        (device if next(iter(patch)) in device else voice).update(patch)
    seen=[]
    async def handler(request):
        assert request.method=='GET' and request.url.host=='127.0.0.1' and request.url.port==8000
        assert request.headers['X-Local-Gateway']=='synthetic-grant'
        seen.append(request.url.path)
        return httpx.Response(200,json={'success':True,'data':device if request.url.path.endswith('device/status') else voice})
    reader=DeviceStatusReader(api_key='synthetic-key',local_delegation='synthetic-grant',
        expected_serial='synthetic',transport=httpx.MockTransport(handler))
    if patch:
        with pytest.raises(ValueError): await reader.verify_network_idle()
    else:
        await reader.verify_network_idle()
    assert set(seen)=={'/api/v1/device/status','/api/v1/dialog/status'}
