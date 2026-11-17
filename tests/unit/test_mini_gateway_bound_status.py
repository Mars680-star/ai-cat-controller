"""Ownership and the persisted full-device identity must both match the route."""

import hashlib

from fastapi.testclient import TestClient
import pytest

from ai_cat_controller.mini_gateway.app import create_app
from ai_cat_controller.mini_gateway.config import MiniGatewayConfig
from ai_cat_controller.mini_gateway.store import SessionStore


APP_ID = "wx1234567890abcdef"
SERIAL = "7c2b63fd4a128"
OTHER_SERIAL_SAME_MASK = "7c2DIFFERENTa128"


class Provider:
    async def exchange(self, code):
        return code


class Reader:
    expected_serial = SERIAL

    def __init__(self):
        self.calls = 0
        self.during_read = lambda: None

    async def read(self):
        self.calls += 1
        self.during_read()
        return {"readOnly": True, "device": {"deviceSerial": "7c2****a128"}}


def fingerprint(store, pet_id, serial):
    with store._connect() as connection:
        connection.execute("INSERT OR REPLACE INTO mg_meta(key,value) VALUES(?,?)", (
            "device:" + pet_id, hashlib.sha256(serial.encode("ascii")).hexdigest(),
        ))


@pytest.fixture
def bound(tmp_path):
    store = SessionStore(tmp_path / "gateway.sqlite3")
    reader = Reader()
    app = create_app(MiniGatewayConfig(db_path=store.path, app_id=APP_ID, app_secret="a" * 32, status_pet_id="pet_one"), provider=Provider(), store=store, status_reader=reader)
    with TestClient(app) as client:
        session = client.post("/v1/auth/wechat", json={"code": "owner"}).json()["data"]
        headers = {"Authorization": "Bearer " + session["accessToken"]}
        store.grant_pet(session["userId"], "pet_one", "小安")
        yield client, store, reader, headers, session["userId"]


def test_legacy_ownership_without_device_registration_cannot_read_pinned_route(bound):
    client, store, reader, headers, _ = bound
    response = client.get("/v1/pets/pet_one/status", headers=headers)
    assert response.status_code == 503
    assert reader.calls == 0


def test_registered_full_identity_permits_only_the_explicit_route(bound):
    client, store, reader, headers, _ = bound
    fingerprint(store, "pet_one", SERIAL)
    response = client.get("/v1/pets/pet_one/status", headers=headers)
    assert response.status_code == 200
    assert reader.calls == 1


def test_same_mask_different_full_identity_is_rejected_before_upstream_read(bound):
    client, store, reader, headers, _ = bound
    fingerprint(store, "pet_one", OTHER_SERIAL_SAME_MASK)
    response = client.get("/v1/pets/pet_one/status", headers=headers)
    assert response.status_code == 503
    assert reader.calls == 0


def test_other_wechat_user_cannot_probe_registration_or_read_device(bound):
    client, store, reader, _, _ = bound
    fingerprint(store, "pet_one", SERIAL)
    other = client.post("/v1/auth/wechat", json={"code": "other"}).json()["data"]
    response = client.get("/v1/pets/pet_one/status", headers={"Authorization": "Bearer " + other["accessToken"]})
    assert response.status_code == 404
    assert reader.calls == 0


def test_changed_registration_while_sampling_never_returns_the_old_device(bound):
    client, store, reader, headers, _ = bound
    fingerprint(store, "pet_one", SERIAL)
    reader.during_read = lambda: fingerprint(store, "pet_one", OTHER_SERIAL_SAME_MASK)
    response = client.get("/v1/pets/pet_one/status", headers=headers)
    assert response.status_code == 503
    assert reader.calls == 1


def test_revoked_ownership_while_sampling_does_not_leak_state(bound):
    client, store, reader, headers, user_id = bound
    fingerprint(store, "pet_one", SERIAL)
    reader.during_read = lambda: store.revoke_pet(user_id, "pet_one")
    response = client.get("/v1/pets/pet_one/status", headers=headers)
    assert response.status_code == 404


def test_unpinned_reader_cannot_bypass_registered_device_identity(bound):
    client, store, reader, headers, _ = bound
    fingerprint(store, "pet_one", SERIAL)
    reader.expected_serial = None
    response = client.get("/v1/pets/pet_one/status", headers=headers)
    assert response.status_code == 503
    assert reader.calls == 0


def test_session_revoked_during_sampling_does_not_deliver_status(bound):
    client, store, reader, headers, _ = bound
    fingerprint(store, "pet_one", SERIAL)
    token = headers["Authorization"].split(" ", 1)[1]
    reader.during_read = lambda: store.revoke_session(token, APP_ID)
    response = client.get("/v1/pets/pet_one/status", headers=headers)
    assert response.status_code == 401
    assert "readOnly" not in response.text


def test_session_expired_during_sampling_does_not_deliver_status(bound):
    client, store, reader, headers, _ = bound
    fingerprint(store, "pet_one", SERIAL)
    now = store.clock()
    reader.during_read = lambda: setattr(store, "clock", lambda: now + 3601)
    response = client.get("/v1/pets/pet_one/status", headers=headers)
    assert response.status_code == 401
    assert "readOnly" not in response.text
