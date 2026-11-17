"""Actual lifecycle HTTP boundaries, synthetic sessions and temporary ledgers only."""
import hashlib
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from ai_cat_controller.mini_gateway.app import create_app
from ai_cat_controller.mini_gateway.config import MiniGatewayConfig
from ai_cat_controller.mini_gateway.lifecycle import LocalLifecycleService, LifecycleOutcome, LifecycleProof
from ai_cat_controller.mini_gateway.lifecycle_models import SCOPES
from ai_cat_controller.mini_gateway.store import SessionStore

APP, SERIAL, PET = 'wx1234567890abcdef', 'K1-SYNTHETIC-ROUTES', 'pet_mini'


class Executor:
    def __init__(self):
        self.calls = []

    async def probe(self, *, nonce, kind, scope, **_):
        import time
        return LifecycleProof(nonce, kind, scope, hashlib.sha256(SERIAL.encode()).hexdigest(),
                              'a' * 64, 'b' * 64, 'c' * 64, time.monotonic() + 2, object())

    async def execute(self, *, request_id, kind, scope, **_):
        self.calls.append(kind)
        return LifecycleOutcome(request_id, kind, scope, 'completed')


def fixture(tmp_path, *, executor=None, attached=True):
    store = SessionStore(tmp_path / 'identity' / 'session.sqlite3')
    issued = store.issue_session(APP, 'synthetic-openid', 3600)
    store.grant_pet(issued.user_id, PET, '小安')
    with store._connect() as c:
        c.execute('INSERT INTO mg_meta VALUES(?,?)', ('device:' + PET, hashlib.sha256(SERIAL.encode()).hexdigest()))
    lifecycle = LocalLifecycleService(db_path=tmp_path / 'private' / 'lifecycle.sqlite3',
        expected_serial=SERIAL, product_pet_id='pet_web', expected_owner_sha256='a' * 64,
        enabled_kinds=tuple(SCOPES) if executor else (), executor=executor,
        operator_check=lambda **_: True)
    app = create_app(MiniGatewayConfig(db_path=store.path, app_id=APP, app_secret='a' * 32,
                                     status_pet_id=PET), store=store,
                     status_reader=SimpleNamespace(expected_serial=SERIAL),
                     lifecycle_service=lifecycle if attached else None)
    return app, lifecycle, store, issued


def headers(issued):
    return {'Authorization': 'Bearer ' + issued.access_token}


def challenge_body(kind='memory_clear'):
    return {'kind': kind, 'scope': SCOPES[kind]}


def test_default_injected_service_is_a_readonly_unready_capability_not_legacy_dispatch(tmp_path):
    app, lifecycle, _, issued = fixture(tmp_path)
    with TestClient(app) as client:
        result = client.get('/v1/pets/' + PET + '/lifecycle', headers=headers(issued))
        assert result.status_code == 200
        value = result.json()['data']
        assert not value['verified'] and all(not operation['available'] for operation in value['operations'])
        rejected = client.post('/v1/pets/' + PET + '/lifecycle/challenges', headers=headers(issued), json=challenge_body())
        assert rejected.status_code in {409, 503}
        assert client.get('/health').json()['data']['readOnly'] is True
        assert not lifecycle._tasks


def test_unconfigured_and_missing_auth_are_not_allowed(tmp_path):
    app, lifecycle, store, issued = fixture(tmp_path, attached=False)
    with TestClient(app) as client:
        assert client.get('/v1/pets/' + PET + '/lifecycle').status_code == 401
        assert client.get('/v1/pets/' + PET + '/lifecycle', headers=headers(issued)).status_code == 503
    lifecycle.close()


def test_other_owner_and_incorrect_registered_device_are_rejected_before_probing(tmp_path):
    executor = Executor()
    app, _, store, issued = fixture(tmp_path, executor=executor)
    with TestClient(app) as client:
        other = store.issue_session(APP, 'unrelated-openid', 3600)
        route = '/v1/pets/' + PET + '/lifecycle'
        assert client.get(route, headers=headers(other)).status_code == 404
        assert client.post(route + '/challenges', headers=headers(other), json=challenge_body()).status_code == 404
        with store._connect() as c:
            c.execute('UPDATE mg_meta SET value=? WHERE key=?', ('d' * 64, 'device:' + PET))
        assert client.get(route, headers=headers(issued)).status_code == 503
        assert not executor.calls


def test_lifecycle_four_kib_budget_does_not_expand_login_or_get_routes(tmp_path):
    app, lifecycle, _, issued = fixture(tmp_path)
    with TestClient(app) as client:
        route = '/v1/pets/' + PET + '/lifecycle/challenges'
        raw = b'{"kind":"memory_clear","scope":"owner_pet_memory"}' + b' ' * 1200
        assert client.post(route, headers=headers(issued), content=raw).status_code == 503
        assert client.post('/v1/auth/wechat', content=b' ' * 1200).status_code == 413
        assert client.get('/v1/pets/' + PET + '/lifecycle/requests/bad$', headers=headers(issued)).status_code == 400
        assert lifecycle._find((issued.user_id, PET, 'one')) is None


@pytest.mark.parametrize('suffix,body', [('', None), ('/challenges', challenge_body()),
    ('/commands', {'requestId': 'one', **challenge_body(), 'challengeId': 'a' * 43, 'confirmVersion': 1})])
def test_query_get_body_duplicate_json_and_strict_methods_rejected_without_intent(tmp_path, suffix, body):
    app, lifecycle, _, issued = fixture(tmp_path)
    route = '/v1/pets/' + PET + '/lifecycle' + suffix
    method = 'GET' if not suffix else 'POST'
    with TestClient(app) as client:
        assert client.request(method, route + '?token=forbidden', headers=headers(issued), json=body).status_code == 400
        assert client.request('GET', route, headers=headers(issued), content=b'{}').status_code == 400
        if suffix:
            raw = b'{"kind":"memory_clear","kind":"unbind","scope":"owner_pet_memory"}'
            assert client.post(route, headers=headers(issued), content=raw).status_code == 400
            assert client.post(route, headers=headers(issued), content=b'x' * 4097).status_code == 413
            assert client.put(route, headers=headers(issued)).status_code == 405
        assert lifecycle._find((issued.user_id, PET, 'one')) is None


@pytest.mark.parametrize('kind', SCOPES)
def test_explicit_synthetic_execution_and_original_receipt_survive_ownership_revocation(tmp_path, kind):
    executor = Executor()
    app, lifecycle, store, issued = fixture(tmp_path, executor=executor)
    route = '/v1/pets/' + PET + '/lifecycle'
    with TestClient(app) as client:
        response = client.post(route + '/challenges', headers=headers(issued), json=challenge_body(kind))
        assert response.status_code == 200
        prepared = response.json()['data']
        body = {'requestId': 'one', 'kind': kind, 'scope': SCOPES[kind],
                'challengeId': prepared['challengeId'], 'confirmVersion': 1}
        result = client.post(route + '/commands', headers=headers(issued), json=body)
        assert result.status_code == 200 and result.json()['data']['status'] == 'completed'
        assert executor.calls == [kind]
        if kind in {'unbind', 'product_reset'}:
            assert not store.authorize(issued.user_id, PET)
        else:
            store.revoke_pet(issued.user_id, PET)
        receipt = client.get(route + '/requests/one', headers=headers(issued))
        assert receipt.status_code == 200 and receipt.json()['data'] == result.json()['data']
        assert client.get(route, headers=headers(issued)).status_code == 404
        other = store.issue_session(APP, 'another-synthetic-openid', 3600)
        assert client.get(route + '/requests/one', headers=headers(other)).status_code == 404
        store.revoke_session(issued.access_token, APP)
        assert client.get(route + '/requests/one', headers=headers(issued)).status_code == 401


def test_fresh_revocation_during_challenge_does_not_return_a_usable_confirmation(tmp_path):
    executor = Executor()
    app, _, store, issued = fixture(tmp_path, executor=executor)
    original = executor.probe
    async def revoke(**kwargs):
        result = await original(**kwargs)
        store.revoke_session(issued.access_token, APP)
        return result
    executor.probe = revoke
    with TestClient(app) as client:
        response = client.post('/v1/pets/' + PET + '/lifecycle/challenges', headers=headers(issued), json=challenge_body())
        assert response.status_code == 401 and not executor.calls


def test_disabled_command_has_no_owner_revocation_or_raw_info_in_error(tmp_path):
    app, _, store, issued = fixture(tmp_path)
    with TestClient(app) as client:
        result = client.post('/v1/pets/' + PET + '/lifecycle/commands', headers=headers(issued),
                             json={'requestId': 'one', **challenge_body('product_reset'),
                                   'challengeId': 'x' * 43, 'confirmVersion': 1})
        assert result.status_code in {409, 503}
        assert store.authorize(issued.user_id, PET)
        assert SERIAL not in result.text and issued.access_token not in result.text
