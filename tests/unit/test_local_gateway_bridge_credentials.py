"""Credential-domain regression for every old business bridge, offline only."""
from pathlib import Path

import httpx
import pytest

from ai_cat_controller.mini_gateway.motion import LocalMotionController
from ai_cat_controller.mini_gateway.product import LocalProductService
from ai_cat_controller.mini_gateway.dialog import LocalDialogReader
from ai_cat_controller.mini_gateway.dialog_control import LocalDialogController
from ai_cat_controller.mini_gateway.preferences import LocalPreferencesService

TOKEN='lgd_'+'a'*43
WEB='synthetic_web_session_0123456789'
ORIGIN='http://127.0.0.1:8000'
SERIAL='K1-LOCAL-CREDENTIAL-001'
KINDS=('motion','product','dialog','control','preferences')


def construct(kind,tmp_path,**extra):
    kwargs=dict(upstream_url=ORIGIN,api_key='synthetic-key',expected_serial=SERIAL,product_pet_id='pet_synthetic')
    if kind!='dialog': kwargs['db_path']=tmp_path/'private'/(kind+'.sqlite3')
    kwargs.update(extra)
    return {'motion':LocalMotionController,'product':LocalProductService,'dialog':LocalDialogReader,
            'control':LocalDialogController,'preferences':LocalPreferencesService}[kind](**kwargs)


@pytest.mark.parametrize('kind',KINDS)
def test_explicit_local_credential_is_distinct_from_webpage_credential(tmp_path,kind):
    service=construct(kind,tmp_path,local_delegation=TOKEN)
    try:
        if kind=='motion': assert service._product_header=='X-Local-Gateway'
        else: assert service._authorization_header=='X-Local-Gateway'
    finally: service.close()


@pytest.mark.parametrize('kind',KINDS)
@pytest.mark.parametrize('extra',[{'product_session':WEB,'local_delegation':TOKEN},
    {'local_delegation':TOKEN,'upstream_url':'http://192.168.1.152:8000'},
    {'local_delegation':TOKEN,'upstream_url':'http://localhost:8000'},
    {'local_delegation':'not-a-delegation'}])
def test_dual_auth_or_nonliteral_local_origin_rejected_before_store_open(tmp_path,kind,extra):
    with pytest.raises(ValueError): construct(kind,tmp_path,**extra)
    assert not (tmp_path/'private').exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['motion','product','dialog','preferences'])
async def test_bounded_requests_use_only_local_header_and_keep_public_health_uncredentialed(tmp_path,kind):
    seen=[]
    async def handler(request):
        seen.append(request)
        assert 'X-Mock-Session' not in request.headers
        if request.url.path=='/health':
            assert 'X-Local-Gateway' not in request.headers and 'X-API-Key' not in request.headers
        else:
            assert request.headers['X-Local-Gateway']==TOKEN and request.headers['X-API-Key']=='synthetic-key'
        data=[] if request.url.path=='/api/v1/pets' else {}
        return httpx.Response(200,json={'success':True,'data':data})
    service=construct(kind,tmp_path,local_delegation=TOKEN,transport=httpx.MockTransport(handler))
    try:
        if kind=='motion':
            await service._request('GET','/health')
            await service._request('GET','/device/status')
            await service._request('GET','/pets',product=True,expect_list=True)
            await service._request('POST','/motion/stop',body={})
        elif kind=='product':
            await service._request('GET','/health')
            await service._request('GET','/device/status')
            await service._request('GET','/pets',expect_list=True)
        elif kind=='dialog':
            await service._get('/health')
            await service._get('/device/status')
            await service._get('/pets',expect_list=True)
        else:
            await service._get('/health')
            await service._get('/device/status')
            await service._get('/pets')
        assert len(seen)>=3
    finally: service.close()
