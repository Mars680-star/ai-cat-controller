"""Isolated business gateway tests: fake identities, temporary DBs, no devices."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import httpx
import pytest
from fastapi.testclient import TestClient


SECRET = "0123456789abcdef0123456789abcdef"
OPENID = "openid-test-never-return"
CODE = "code-test-never-log"
STATUS_SERIAL = "7c2b63fd4a128"


def seeded_registration(store, user_id, pet_id, name, *, serial=STATUS_SERIAL):
    """Only a unit fixture: no production grant route or hardware read."""
    store.grant_pet(user_id, pet_id, name)
    with store._connect() as connection:
        connection.execute("INSERT INTO mg_meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                           ("device:" + pet_id, hashlib.sha256(serial.encode("ascii")).hexdigest()))


class FakeProvider:
    def __init__(self, openid=OPENID, fail=False):
        self.openid = openid
        self.fail = fail
        self.codes = []

    async def exchange(self, code):
        self.codes.append(code)
        await asyncio.sleep(0.005)
        if self.fail:
            raise RuntimeError(f"{SECRET} {code} upstream-secret-response")
        return self.openid


@pytest.fixture
def config(tmp_path):
    from ai_cat_controller.mini_gateway.config import MiniGatewayConfig

    return MiniGatewayConfig(db_path=tmp_path / "mini.db", app_id="wx0123456789abcdef", app_secret=SECRET, status_pet_id="pet-one")


def client_for(config, **kwargs):
    from ai_cat_controller.mini_gateway.app import create_app

    app = create_app(config, **kwargs)
    return TestClient(app), app


def login(client, code=CODE):
    response = client.post("/v1/auth/wechat", json={"code": code})
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert set(data) == {"accessToken", "expiresAt", "userId"}
    assert data["expiresAt"].endswith("Z")
    assert datetime.fromisoformat(data["expiresAt"].replace("Z", "+00:00")).tzinfo
    return data, {"Authorization": "Bearer " + data["accessToken"]}


def test_missing_credentials_never_fake_login_or_call_provider(tmp_path):
    from ai_cat_controller.mini_gateway.config import MiniGatewayConfig

    provider = FakeProvider()
    client, _ = client_for(MiniGatewayConfig(db_path=tmp_path / "new.db"), provider=provider)
    with client:
        health = client.get("/health")
        response = client.post("/v1/auth/wechat", json={"code": CODE})
    assert health.json() == {"success": True, "data": {"mode": "local-development", "wechatConfigured": False, "readOnly": True}}
    assert response.status_code == 503
    assert not provider.codes
    assert CODE not in response.text


def test_login_session_hash_only_and_logout(config, caplog):
    provider = FakeProvider()
    client, app = client_for(config, provider=provider)
    with client, caplog.at_level(logging.DEBUG):
        data, headers = login(client)
        session = client.get("/v1/session", headers=headers)
        assert session.json() == {"success": True, "data": {"userId": data["userId"], "expiresAt": data["expiresAt"]}}
        assert client.post("/v1/auth/logout", headers=headers).json() == {"success": True, "data": {"loggedOut": True}}
        assert client.get("/v1/session", headers=headers).status_code == 401
    assert len(data["accessToken"]) >= 32
    raw = config.db_path.read_bytes()
    assert data["accessToken"].encode() not in raw
    assert CODE.encode() not in raw
    assert SECRET.encode() not in raw
    assert config.db_path.stat().st_mode & 0o777 == 0o600
    for sensitive in (SECRET, CODE, OPENID, data["accessToken"]):
        assert sensitive not in caplog.text
    assert "main" not in app.__module__


def test_code_is_consumed_persistently_and_not_retried_after_provider_error(config):
    provider = FakeProvider(fail=True)
    client, _ = client_for(config, provider=provider)
    with client:
        first = client.post("/v1/auth/wechat", json={"code": CODE})
    assert first.status_code == 503
    assert SECRET not in first.text and CODE not in first.text
    second_provider = FakeProvider()
    client, _ = client_for(config, provider=second_provider)
    with client:
        second = client.post("/v1/auth/wechat", json={"code": CODE})
    assert second.status_code == 409
    assert not second_provider.codes


def test_concurrent_code_reuse_calls_provider_once(config):
    provider = FakeProvider()
    client, _ = client_for(config, provider=provider)
    with client, ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: client.post("/v1/auth/wechat", json={"code": CODE}), range(2)))
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert provider.codes == [CODE]


def test_existing_session_survives_provider_failure_and_new_app_instance(config):
    client, _ = client_for(config, provider=FakeProvider())
    with client:
        data, headers = login(client)
    client, _ = client_for(config, provider=FakeProvider(fail=True))
    with client:
        response = client.get("/v1/session", headers=headers)
    assert response.status_code == 200
    assert response.json()["data"]["userId"] == data["userId"]


def test_expiry_and_app_id_identity_isolation(config):
    from dataclasses import replace
    from ai_cat_controller.mini_gateway.store import SessionStore

    clock = [1000.0]
    store = SessionStore(config.db_path, clock=lambda: clock[0])
    client, _ = client_for(config, provider=FakeProvider(), store=store)
    with client:
        original, headers = login(client)
        clock[0] += config.session_ttl_seconds
        assert client.get("/v1/session", headers=headers).status_code == 401
    other_config = replace(config, app_id="wxfedcba9876543210")
    client, _ = client_for(other_config, provider=FakeProvider(), store=store)
    with client:
        other, _ = login(client)
        assert client.get("/v1/session", headers=headers).status_code == 401
    assert original["userId"] != other["userId"]


def test_ownership_isolation_and_single_reader_mapping(config):
    class Reader:
        expected_serial = STATUS_SERIAL

        def __init__(self):
            self.calls = 0

        async def read(self):
            self.calls += 1
            return {"readOnly": True, "device": {"batteryPercent": 83}}

    reader = Reader()
    client, app = client_for(config, provider=FakeProvider(), status_reader=reader)
    with client:
        first, first_headers = login(client, "first-code")
        seeded_registration(app.state.store, first["userId"], "pet-one", "演示狐狸")
        seeded_registration(app.state.store, first["userId"], "pet-two", "另一台狐狸", serial="7c2b63fd4a120")
        assert client.get("/v1/pets", headers=first_headers).json()["data"] == [{"petId": "pet-one", "name": "演示狐狸"}, {"petId": "pet-two", "name": "另一台狐狸"}]
        response = client.get("/v1/pets/pet-one/status", headers=first_headers)
        assert response.json()["data"] == {"readOnly": True, "device": {"batteryPercent": 83}}
        assert client.get("/v1/pets/pet-two/status", headers=first_headers).status_code == 503
        second_client, _ = client_for(config, provider=FakeProvider(openid="other-openid"))
        with second_client:
            _, second_headers = login(second_client, "second-code")
            assert second_client.get("/v1/pets", headers=second_headers).json()["data"] == []
        for pet in ("pet-one", "unknown-pet"):
            assert client.get(f"/v1/pets/{pet}/status", headers=second_headers).status_code == 404
    assert reader.calls == 1


def test_no_reader_no_mock_and_safe_reader_failure(config):
    class BrokenReader:
        expected_serial = STATUS_SERIAL
        calls = 0

        async def read(self):
            self.calls += 1
            raise RuntimeError(SECRET)

    client, app = client_for(config, provider=FakeProvider())
    with client:
        data, headers = login(client)
        seeded_registration(app.state.store, data["userId"], "pet-one", "狐狸")
        assert client.get("/v1/pets/pet-one/status", headers=headers).status_code == 503
        app.state.status_reader = BrokenReader()
        response = client.get("/v1/pets/pet-one/status", headers=headers)
    assert response.status_code == 503
    assert SECRET not in response.text
    assert "battery" not in response.text
    assert app.state.status_reader.calls == 1


def test_pet_has_one_owner_and_revocation_blocks_old_session(config):
    class Reader:
        expected_serial = STATUS_SERIAL
        calls = 0

        async def read(self):
            self.calls += 1
            return {"readOnly": True, "value": "status"}

    client, app = client_for(config, provider=FakeProvider(), status_reader=Reader())
    with client:
        old, old_headers = login(client, "old-owner-code")
        app.state.provider = FakeProvider(openid="new-owner-openid")
        new, new_headers = login(client, "new-owner-code")
        seeded_registration(app.state.store, old["userId"], "pet-one", "狐狸")
        with pytest.raises(ValueError):
            app.state.store.grant_pet(new["userId"], "pet-one", "不可抢占")
        assert client.get("/v1/pets/pet-one/status", headers=old_headers).status_code == 200
        app.state.store.revoke_pet(old["userId"], "pet-one")
        seeded_registration(app.state.store, new["userId"], "pet-one", "新归属")
        assert client.get("/v1/pets/pet-one/status", headers=old_headers).status_code == 404
        assert client.get("/v1/pets/pet-one/status", headers=new_headers).status_code == 200
    assert app.state.status_reader.calls == 2


def test_revocation_during_reader_call_does_not_deliver_status(config):
    client, app = client_for(config, provider=FakeProvider())
    with client:
        owner, headers = login(client)
        seeded_registration(app.state.store, owner["userId"], "pet-one", "狐狸")

        class Reader:
            expected_serial = STATUS_SERIAL
            calls = 0

            async def read(self):
                self.calls += 1
                app.state.store.revoke_pet(owner["userId"], "pet-one")
                return {"must-not-return": "revoked-device-data"}

        app.state.status_reader = Reader()
        response = client.get("/v1/pets/pet-one/status", headers=headers)
    assert response.status_code == 404
    assert "revoked-device-data" not in response.text
    assert app.state.status_reader.calls == 1


@pytest.mark.parametrize("body", [
    {"code": CODE, "openid": OPENID}, {"code": ""}, {"code": 123},
    {"code": "x" * 257}, {"code": "bad\ncode"}, [], None,
])
def test_login_rejects_untrusted_body_without_echoing_code(config, body):
    provider = FakeProvider()
    client, _ = client_for(config, provider=provider)
    with client:
        response = client.post("/v1/auth/wechat", json=body)
    assert response.status_code == 400
    assert CODE not in response.text and OPENID not in response.text
    assert not provider.codes


def test_body_size_query_tokens_and_missing_auth_are_rejected(config):
    provider = FakeProvider()
    client, _ = client_for(config, provider=provider)
    with client:
        assert client.post("/v1/auth/wechat", content=b"x" * 1025).status_code == 413
        assert client.post("/v1/auth/wechat?code=secret", json={"code": CODE}).status_code == 400
        for path in ("/v1/session", "/v1/pets", "/v1/pets/pet-one/status"):
            assert client.get(path).status_code == 401
            assert client.get(path + "?access_token=secret").status_code == 400
        assert client.post("/v1/pets").status_code == 405
        assert client.post("/v1/pets", json={"petId": "claim-somebody"}).status_code == 400
        assert client.post("/v1/admin/grant").status_code == 404
    assert not provider.codes


def test_persistent_per_client_login_rate_limit(config):
    provider = FakeProvider()
    client, _ = client_for(config, provider=provider)
    with client:
        responses = [client.post("/v1/auth/wechat", json={"code": f"code-{i}"}) for i in range(11)]
    assert [response.status_code for response in responses[:10]] == [200] * 10
    assert responses[-1].status_code == 429
    client, _ = client_for(config, provider=provider)
    with client:
        assert client.post("/v1/auth/wechat", json={"code": "later-code"}).status_code == 429
    assert len(provider.codes) == 10


@pytest.mark.parametrize("kind", ["business_db", "public_db", "symlink", "known_product_name"])
def test_store_never_opens_existing_product_or_unsafe_database(tmp_path, kind):
    from ai_cat_controller.mini_gateway.store import SessionStore

    path = tmp_path / ("product-data.db" if kind == "known_product_name" else "mini.db")
    with sqlite3.connect(path) as connection:
        if kind == "business_db":
            connection.execute("CREATE TABLE product_users(id TEXT)")
    path.chmod(0o644 if kind == "public_db" else 0o600)
    if kind == "symlink":
        link = tmp_path / "alias.db"
        link.symlink_to(path)
        path = link
    before = path.read_bytes()
    with pytest.raises((ValueError, RuntimeError)):
        SessionStore(path)
    assert path.read_bytes() == before


@pytest.mark.parametrize("ttl", [0, 59, 86401, True])
def test_config_rejects_unsafe_session_ttl(tmp_path, ttl):
    from ai_cat_controller.mini_gateway.config import MiniGatewayConfig

    with pytest.raises(ValueError):
        MiniGatewayConfig(db_path=tmp_path / "mini.db", session_ttl_seconds=ttl)


@pytest.mark.parametrize("change", [{"app_id": "fake-app"}, {"app_secret": "fake-secret"}, {"app_secret": SECRET + "\n"}])
def test_config_rejects_malformed_wechat_settings(tmp_path, change):
    from ai_cat_controller.mini_gateway.config import MiniGatewayConfig

    with pytest.raises(ValueError):
        MiniGatewayConfig(db_path=tmp_path / "mini.db", **change)


def test_sessions_do_not_depend_on_secret_or_provider_availability(config):
    from dataclasses import replace

    client, _ = client_for(config, provider=FakeProvider())
    with client:
        _, headers = login(client)
    client, _ = client_for(replace(config, app_secret=""))
    with client:
        assert client.get("/v1/session", headers=headers).status_code == 200
        assert client.post("/v1/auth/wechat", json={"code": "new-code"}).status_code == 503


def test_wal_product_database_is_rejected_without_changing_any_files(tmp_path):
    from ai_cat_controller.mini_gateway.store import SessionStore

    path = tmp_path / "unrelated.db"
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE product_users(id TEXT)")
        connection.execute("INSERT INTO product_users VALUES('keep')")
        connection.commit()
        path.chmod(0o600)
        paths = [path, tmp_path / "unrelated.db-wal", tmp_path / "unrelated.db-shm"]
        before = {file.name: file.read_bytes() for file in paths}
        with pytest.raises(ValueError):
            SessionStore(path)
        assert {file.name: file.read_bytes() for file in paths} == before
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_wechat_provider_only_extracts_openid_and_does_not_log_secrets(caplog):
    from ai_cat_controller.mini_gateway.wechat import WeChatCodeProvider

    requests = []

    def dispatch(request):
        requests.append(request)
        return httpx.Response(200, json={"openid": OPENID, "session_key": "private-wx-session", "unionid": "private-union-id"})

    provider = WeChatCodeProvider(app_id="wx-test-app", app_secret=SECRET, transport=httpx.MockTransport(dispatch))
    with caplog.at_level(logging.DEBUG):
        result = await provider.exchange(CODE)
    assert result == OPENID
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET"
    assert request.url.scheme == "https" and request.url.host == "api.weixin.qq.com"
    assert request.url.path == "/sns/jscode2session"
    assert request.url.params["js_code"] == CODE
    assert request.url.params["secret"] == SECRET
    assert request.url.params["grant_type"] == "authorization_code"
    assert request.headers["accept-encoding"] == "identity"
    for sensitive in (SECRET, CODE, OPENID, "private-wx-session", "private-union-id"):
        assert sensitive not in caplog.text
        assert sensitive not in repr(provider)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["redirect", "bad_json", "too_large", "api_error", "transport_error", "invalid_openid"])
async def test_wechat_provider_errors_are_safe_and_never_follow_redirects(failure, caplog):
    from ai_cat_controller.mini_gateway.wechat import WeChatCodeProvider, WeChatUnavailable

    requests = []

    def dispatch(request):
        requests.append(request)
        if failure == "transport_error":
            raise httpx.ConnectError(f"{SECRET} {CODE}", request=request)
        if failure == "redirect":
            return httpx.Response(302, headers={"Location": "http://127.0.0.1/secret"})
        if failure == "bad_json":
            return httpx.Response(200, text=SECRET)
        if failure == "too_large":
            return httpx.Response(200, json={"openid": OPENID, "extra": "x" * 32768})
        if failure == "invalid_openid":
            return httpx.Response(200, json={"openid": "bad\nopenid"})
        return httpx.Response(200, json={"errcode": 40029, "errmsg": SECRET})

    provider = WeChatCodeProvider(app_id="wx-test-app", app_secret=SECRET, transport=httpx.MockTransport(dispatch))
    with caplog.at_level(logging.DEBUG), pytest.raises(WeChatUnavailable) as error:
        await provider.exchange(CODE)
    assert len(requests) == 1
    assert SECRET not in str(error.value) and CODE not in str(error.value)
    assert SECRET not in caplog.text and CODE not in caplog.text
    assert error.value.__cause__ is None and error.value.__context__ is None


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [{"Content-Encoding": "gzip"}, {"Content-Length": "32769"}, {"Content-Length": "bad"}])
async def test_provider_rejects_compression_and_unsafe_lengths_before_reading(headers):
    from ai_cat_controller.mini_gateway.wechat import WeChatCodeProvider, WeChatUnavailable

    class NeverRead(httpx.AsyncByteStream):
        async def __aiter__(self):
            pytest.fail("Unsafe provider response must not be read")
            yield b""

    provider = WeChatCodeProvider(app_id="wx0123456789abcdef", app_secret=SECRET,
                                 transport=httpx.MockTransport(lambda request: httpx.Response(200, headers=headers, stream=NeverRead())))
    with pytest.raises(WeChatUnavailable):
        await provider.exchange(CODE)


@pytest.mark.asyncio
async def test_default_provider_transport_disables_environment_and_retries(monkeypatch):
    from ai_cat_controller.mini_gateway import wechat

    calls = []

    def transport(**kwargs):
        calls.append(kwargs)
        return httpx.MockTransport(lambda request: httpx.Response(200, json={"openid": OPENID}))

    monkeypatch.setattr(wechat.httpx, "AsyncHTTPTransport", transport)
    provider = wechat.WeChatCodeProvider(app_id="wx0123456789abcdef", app_secret=SECRET)
    assert await provider.exchange(CODE) == OPENID
    assert calls == [{"trust_env": False, "retries": 0}]
