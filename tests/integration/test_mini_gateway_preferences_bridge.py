"""Preferences bridge against real APIs and temporary SQLite, no device/network.

Only hardware identity is simulated. Settings, volume, dialog configuration and
feedback reach actual services backed by MockAiCatAdapter and pytest databases.
"""

import hashlib
import json
import sqlite3

import httpx
import pytest

from ai_cat_controller.adapters.mock import MockAiCatAdapter
from ai_cat_controller.mini_gateway.app import create_app
from ai_cat_controller.mini_gateway.config import MiniGatewayConfig
from ai_cat_controller.mini_gateway.preferences import LocalPreferencesService
from ai_cat_controller.mini_gateway.store import SessionStore


SERIAL = "K1-PREFERENCES-BRIDGE-001"
MINI_USER, MINI_PET = "usr_pref_bridge", "pet_pref_bridge"


class PreferencesBridge:
    def __init__(self, client, session, pet, transport):
        self.client, self.session, self.pet = client, session, pet
        self.transport = transport
        self.requests = []
        self.serial = SERIAL
        self.corrupt_followup_ack = False
        self.disconnect_after_pet = False

    @property
    def writes(self):
        return [request for request in self.requests if request.method != "GET"]

    async def __call__(self, request):
        self.requests.append(request)
        path = request.url.path
        if path == "/health":
            return httpx.Response(200, json={"success": True, "data": {"status": "ok", "adapter": "local_k1"}})
        if path == "/api/v1/device/status":
            response = await self.transport.handle_async_request(request)
            body = json.loads(await response.aread())
            body["data"].update(adapter_mode="local_k1", connected=True, device_serial=self.serial)
            return httpx.Response(response.status_code, json=body)
        assert path in {"/api/v1/pets", "/api/v1/dialog/config", f"/api/v1/pets/{self.pet}/settings",
                        f"/api/v1/pets/{self.pet}/feedback"}
        if path.startswith("/api/v1/pets"):
            assert request.headers["X-Mock-Session"] == self.session
        else:
            assert "X-Mock-Session" not in request.headers
        response = await self.transport.handle_async_request(request)
        if self.disconnect_after_pet and request.method == "PATCH" and path.endswith("/settings"):
            self.serial = "ANOTHER-SYNTHETIC-K1"
        if self.corrupt_followup_ack and request.method == "PATCH" and path == "/api/v1/dialog/config":
            body = json.loads(await response.aread())
            body["data"]["follow_up_seconds"] = 119
            return httpx.Response(response.status_code, json=body)
        return response


@pytest.fixture
def preferences_bridge(client, product_app_transport):
    assert isinstance(client.app.state.services.adapter, MockAiCatAdapter)
    login = client.post("/api/v1/auth/mock-login", json={"login_code": "preferences-bridge-owner", "nickname": "合成设置用户"})
    assert login.status_code == 200
    session = login.json()["data"]["session_token"]
    bound = client.post("/api/v1/pets/bind", headers={"X-Mock-Session": session}, json={
        "device_serial": SERIAL, "pet_name": "合成偏好猫", "network_name": "Synthetic",
    })
    assert bound.status_code == 200
    return PreferencesBridge(client, session, bound.json()["data"]["pet"]["pet_id"], product_app_transport)


def _service(tmp_path, bridge, *, settings=True, feedback=True):
    return LocalPreferencesService(db_path=tmp_path / "private-preferences" / "preferences.sqlite3",
        settings_enabled=settings, feedback_enabled=feedback,
        upstream_url="http://127.0.0.1:18000", api_key="synthetic-preferences-key",
        expected_serial=SERIAL, product_session=bridge.session, product_pet_id=bridge.pet,
        transport=httpx.MockTransport(bridge))


async def _authorize():
    return None


@pytest.mark.asyncio
async def test_real_settings_snapshot_preserves_boosted_output_without_writing_device_data(
    tmp_path, preferences_bridge, settings,
):
    bridge = preferences_bridge
    bridge.client.app.state.services.adapter.output_volume_percent = 125
    with sqlite3.connect(settings.data_path) as connection:
        before = list(connection.iterdump())
    service = _service(tmp_path, bridge)
    try:
        result = await service.snapshot(user_id=MINI_USER, pet_id=MINI_PET)
        assert result["verified"] is True and result["name"] == "合成偏好猫"
        assert result["outputVolume"] == 125 and 0 <= result["storedVolume"] <= 100
        assert result["outputVolume"] != result["storedVolume"]
        assert result["followUpSeconds"] == 25 and result["followUpSource"] == "default"
        assert not settings.dialog_config_path.exists() and not bridge.writes
        with sqlite3.connect(settings.data_path) as connection:
            assert list(connection.iterdump()) == before
        public = json.dumps(result, ensure_ascii=False)
        assert all(secret not in public for secret in (SERIAL, bridge.session, bridge.pet, "synthetic-preferences-key"))
    finally:
        service.close()


@pytest.mark.asyncio
async def test_real_settings_two_step_receipt_deduplicates_without_saving_plaintext_in_gateway(
    tmp_path, preferences_bridge,
):
    bridge = preferences_bridge
    service = _service(tmp_path, bridge)
    changes = {"name": "真正合成新名字", "volume": 27, "followUpSeconds": 45}
    arguments = dict(user_id=MINI_USER, pet_id=MINI_PET, request_id="settings-once",
        kind="settings", changes=changes, authorize=_authorize)
    try:
        result = await service.submit(**arguments)
        assert result["status"] == "recorded"
        assert [item["step"] for item in result["steps"]] == ["pet_settings", "follow_up"]
        assert all(item["status"] == "applied" for item in result["steps"])
        assert [request.url.path for request in bridge.writes] == [
            f"/api/v1/pets/{bridge.pet}/settings", "/api/v1/dialog/config",
        ]
        assert json.loads(bridge.writes[0].content) == {"name": changes["name"], "volume": 27}
        assert json.loads(bridge.writes[1].content) == {"follow_up_seconds": 45}
        assert await service.submit(**arguments) == result
        assert await service.receipt(user_id=MINI_USER, pet_id=MINI_PET, request_id="settings-once") == result
        assert len(bridge.writes) == 2
        current = await service.snapshot(user_id=MINI_USER, pet_id=MINI_PET)
        assert current["name"] == changes["name"] and current["storedVolume"] == current["outputVolume"] == 27
        assert current["followUpSeconds"] == 45 and current["followUpSource"] == "persisted"
        with sqlite3.connect(service.path) as connection:
            saved = "\n".join(connection.iterdump())
        assert changes["name"] not in saved and bridge.session not in saved
    finally:
        service.close()


@pytest.mark.asyncio
async def test_known_partial_application_is_not_all_success_and_allows_only_new_confirmed_repair(
    tmp_path, preferences_bridge,
):
    bridge = preferences_bridge
    bridge.disconnect_after_pet = True
    service = _service(tmp_path, bridge)
    arguments = dict(user_id=MINI_USER, pet_id=MINI_PET, request_id="known-partial", kind="settings",
        changes={"name": "部分成功合成名", "followUpSeconds": 45}, authorize=_authorize)
    try:
        result = await service.submit(**arguments)
        assert result["status"] == "partially_applied"
        assert [item["status"] for item in result["steps"]] == ["applied", "not_sent"]
        assert len(bridge.writes) == 1
        assert await service.submit(**arguments) == result and len(bridge.writes) == 1
        bridge.serial, bridge.disconnect_after_pet = SERIAL, False
        fresh = await service.snapshot(user_id=MINI_USER, pet_id=MINI_PET)
        assert fresh["name"] == "部分成功合成名" and fresh["followUpSeconds"] == 25
        assert fresh["settingsReviewRequired"] is False
        repair = await service.submit(user_id=MINI_USER, pet_id=MINI_PET, request_id="explicit-new-repair",
            kind="settings", changes={"followUpSeconds": 45}, authorize=_authorize)
        assert repair["status"] == "recorded" and len(bridge.writes) == 2
        assert bridge.writes[-1].url.path == "/api/v1/dialog/config"
    finally:
        service.close()


@pytest.mark.asyncio
async def test_actual_config_delivery_with_wrong_ack_is_unknown_and_feedback_remains_independent(
    tmp_path, preferences_bridge,
):
    bridge = preferences_bridge
    bridge.corrupt_followup_ack = True
    service = _service(tmp_path, bridge)
    try:
        result = await service.submit(user_id=MINI_USER, pet_id=MINI_PET, request_id="unknown-settings",
            kind="settings", changes={"name": "未知回执合成名", "followUpSeconds": 45}, authorize=_authorize)
        assert result["status"] == "unknown"
        assert [item["status"] for item in result["steps"]] == ["applied", "unknown"]
        assert bridge.client.get("/api/v1/dialog/config").json()["data"]["follow_up_seconds"] == 45
        snapshot = await service.snapshot(user_id=MINI_USER, pet_id=MINI_PET)
        assert snapshot["settingsReviewRequired"] and not snapshot["feedbackReviewRequired"]
        assert snapshot["canSubmitFeedback"] is True and snapshot["canChangeName"] is False
        body = "这是独立反馈，不是重新发送设置"
        receipt = await service.submit(user_id=MINI_USER, pet_id=MINI_PET, request_id="independent-feedback",
            kind="feedback", category="other", content=body, authorize=_authorize)
        assert receipt["status"] == "recorded" and len(bridge.writes) == 3
        assert (await service.snapshot(user_id=MINI_USER, pet_id=MINI_PET))["settingsReviewRequired"] is True
    finally:
        service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("escaped_json", [False, True])
@pytest.mark.parametrize("content", ["问题" * 250, "第一行问题\n第二行\t已复现"])
async def test_whole_mini_feedback_route_records_full_unicode_or_multiline_once_with_private_receipt(
    tmp_path, preferences_bridge, settings, escaped_json, content,
):
    bridge = preferences_bridge
    service = _service(tmp_path, bridge)
    store = SessionStore(tmp_path / "sessions.sqlite3")
    app_id = "wx1234567890abcdef"
    identity = store.issue_session(app_id, "synthetic-preferences-openid", 3600)
    store.grant_pet(identity.user_id, MINI_PET, "合成偏好猫")
    with store._connect() as connection:
        connection.execute("INSERT INTO mg_meta VALUES(?,?)", (
            "device:" + MINI_PET, hashlib.sha256(SERIAL.encode()).hexdigest(),
        ))
    app = create_app(MiniGatewayConfig(db_path=store.path, app_id=app_id, status_pet_id=MINI_PET),
        store=store, status_reader=type("StatusIdentity", (), {"expected_serial": SERIAL})(), preferences_service=service)
    headers = {"Authorization": "Bearer " + identity.access_token, "Content-Type": "application/json"}
    raw = json.dumps({"requestId": "feedback-once", "kind": "feedback", "category": "device", "content": content},
        ensure_ascii=escaped_json).encode("utf-8")
    assert len(raw) < 4096
    if len(content) == 500:
        assert len(raw) > 1024
    base = f"/v1/pets/{MINI_PET}/preferences"
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.post(base + "/commands", content=raw)).status_code == 401
            assert not bridge.writes
            assert (await client.post("/v1/auth/wechat", content=b"x" * 1025)).status_code == 413
            response = await client.post(base + "/commands", headers=headers, content=raw)
            assert response.status_code == 200 and response.json()["data"]["status"] == "recorded"
            assert response.headers["Cache-Control"] == "no-store"
            repeated = await client.post(base + "/commands", headers=headers, content=raw)
            assert repeated.json() == response.json() and len(bridge.writes) == 1
            checked = await client.get(base + "/requests/feedback-once", headers=headers)
            assert checked.status_code == 200 and checked.json()["data"]["status"] == "recorded"
            assert content not in checked.text and bridge.pet not in checked.text
        with sqlite3.connect(settings.data_path) as connection:
            rows = connection.execute("SELECT category,content FROM feedback").fetchall()
        assert rows == [("device", content)]
        with sqlite3.connect(service.path) as connection:
            assert content not in "\n".join(connection.iterdump())
    finally:
        service.close()
