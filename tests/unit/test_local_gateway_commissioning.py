import json
import os

import httpx
import pytest

from ai_cat_controller.local_gateway.commissioning import commission
from ai_cat_controller.local_gateway.configuration import load_configuration
from ai_cat_controller.local_gateway.delegation import DelegationStore
from ai_cat_controller.local_gateway.protocol import LocalProtocolError

BINDING = {'owner_user_id': 'owner_original', 'pet_id': 'pet_original',
    'serial_number': 'original_serial', 'bound_at': '2026-10-30T10:00:00Z'}


@pytest.mark.asyncio
async def test_commission_requires_real_owner_and_never_commands(tmp_path, capsys):
    calls = []
    def handle(request):
        calls.append((request.method, request.url.path))
        assert request.url.host == '127.0.0.1' and request.headers['x-mock-session'] == 'genuine_private_owner_session'
        return httpx.Response(200, json={'success':True,'data':[BINDING]})
    root = tmp_path/'private'
    result = await commission(state_root=root, api_key='private_device_key', owner_session='genuine_private_owner_session',
        product_pet_id='pet_original', serial='original_serial', transport=httpx.MockTransport(handle))
    assert result['commissioned'] and calls == [('GET','/api/v1/pets')]*2
    config = load_configuration(result['configFile'])
    assert config.owner_user_id == 'owner_original' and config.enabled['motion']
    assert os.stat(result['configFile']).st_mode & 0o777 == 0o600
    assert os.stat(result['cardFile']).st_mode & 0o777 == 0o600
    assert config.local_delegation.encode() not in (root/'delegation.sqlite3').read_bytes()
    assert DelegationStore(root/'delegation.sqlite3').resolve(config.local_delegation, BINDING,
        'GET','/api/v1/pets').user_id == 'owner_original'
    assert not capsys.readouterr().out
    with pytest.raises(LocalProtocolError):
        await commission(state_root=root, api_key='private_device_key', owner_session='genuine_private_owner_session',
            product_pet_id='pet_original', serial='original_serial', transport=httpx.MockTransport(handle))


@pytest.mark.asyncio
async def test_expired_or_other_device_owner_never_commissions(tmp_path):
    for status, binding in [(401,BINDING),(200,{**BINDING,'serial_number':'other_serial'})]:
        def handle(request):
            return httpx.Response(status, json={'success':True,'data':[binding]})
        root = tmp_path/str(status)
        with pytest.raises(LocalProtocolError):
            await commission(state_root=root, api_key='private_device_key', owner_session='genuine_private_owner_session',
                product_pet_id='pet_original', serial='original_serial', transport=httpx.MockTransport(handle))
        assert not (root/'current.json').exists() and not (root/'delegation.sqlite3').exists()
