"""Real dialog services behind a synthetic K1 identity, never network/hardware.

Ownership and dedicated read-only probes use the actual product API and temporary
SQLite repository; only health/device identity is relabelled. Command POSTs reach
the real DialogService with MockAiCatAdapter, not pre-scripted success responses.
"""

from __future__ import annotations

import json
import hashlib
import sqlite3

import httpx
import pytest

from ai_cat_controller.adapters.mock import MockAiCatAdapter
from ai_cat_controller.mini_gateway.app import create_app
from ai_cat_controller.mini_gateway.config import MiniGatewayConfig
from ai_cat_controller.mini_gateway.dialog import LocalDialogReader
from ai_cat_controller.mini_gateway.dialog_control import DialogControlError, LocalDialogController
from ai_cat_controller.mini_gateway.store import SessionStore


SERIAL = "K1-DIALOG-BRIDGE-001"
MINI_USER = "usr_dialog_bridge"
MINI_PET = "pet_dialog_bridge"


class DialogBridge:
    def __init__(self, client, session, pet, transport):
        self.client, self.session, self.pet = client, session, pet
        self.requests = []
        self.corrupt_ack = False
        self.legacy = False
        self.serial = SERIAL
        self.transport = transport

    @property
    def posts(self):
        return [request for request in self.requests if request.method == "POST"]

    async def __call__(self, request):
        self.requests.append(request)
        path = request.url.path
        if path == "/health":
            assert request.method == "GET"
            return httpx.Response(200, json={"success": True, "data": {
                "status": "ok", "adapter": "local_k1",
            }})
        if path == "/api/v1/device/status":
            assert request.method == "GET"
            return httpx.Response(200, json={"success": True, "data": {
                "adapter_mode": "local_k1", "connected": True,
                "device_serial": self.serial,
            }})
        if self.legacy and "/dialog-readonly/" in path:
            return httpx.Response(404, json={"detail": "Not Found"})
        if request.method == "POST":
            assert path in {"/api/v1/dialog/text", "/api/v1/dialog/wake", "/api/v1/dialog/interrupt"}
            assert "X-Mock-Session" not in request.headers
        else:
            assert path == "/api/v1/pets" or path.startswith(f"/api/v1/pets/{self.pet}/dialog-readonly/")
        response = await self.transport.handle_async_request(request)
        if self.corrupt_ack and request.method == "POST":
            body = json.loads(await response.aread())
            body["data"]["request_id"] = "wrong-upstream-receipt-id"
            return httpx.Response(response.status_code, json=body)
        return response


@pytest.fixture
def dialog_bridge(client, product_app_transport):
    assert isinstance(client.app.state.services.adapter, MockAiCatAdapter)
    login = client.post("/api/v1/auth/mock-login", json={
        "login_code": "dialog-bridge-owner", "nickname": "合成对话用户",
    })
    assert login.status_code == 200
    session = login.json()["data"]["session_token"]
    response = client.post("/api/v1/pets/bind", headers={"X-Mock-Session": session}, json={
        "device_serial": SERIAL, "pet_name": "合成对话猫", "network_name": "Synthetic",
    })
    assert response.status_code == 200
    return DialogBridge(client, session, response.json()["data"]["pet"]["pet_id"], product_app_transport)


def _controller(tmp_path, bridge, *, enabled=True, session=None):
    return LocalDialogController(
        db_path=tmp_path / "private-dialog" / "dialog.sqlite3", enabled=enabled,
        upstream_url="http://127.0.0.1:18000", api_key="synthetic-dialog-key",
        expected_serial=SERIAL, product_session=session or bridge.session,
        product_pet_id=bridge.pet, transport=httpx.MockTransport(bridge),
    )


async def _authorize():
    return None


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,path,expected_state", [
    ("text", "/api/v1/dialog/text", "thinking"),
    ("listen", "/api/v1/dialog/wake", "listening"),
    ("interrupt", "/api/v1/dialog/interrupt", "interrupted"),
])
async def test_control_bridges_real_dialog_service_and_deduplicates_receipts(
    tmp_path, dialog_bridge, kind, path, expected_state,
):
    bridge = dialog_bridge
    controller = _controller(tmp_path, bridge)
    try:
        snapshot = await controller.snapshot(user_id=MINI_USER, pet_id=MINI_PET)
        assert snapshot["verified"] is True and snapshot["enabled"] is True
        arguments = dict(user_id=MINI_USER, pet_id=MINI_PET, request_id="bridge-once",
                         kind=kind, authorize=_authorize)
        if kind == "text":
            arguments["content"] = "请说一句你好"
        receipt = await controller.submit(**arguments)
        assert receipt["kind"] == kind and receipt["status"] == "accepted"
        assert len(bridge.posts) == 1 and bridge.posts[0].url.path == path
        posted = json.loads(bridge.posts[0].content)
        assert posted["request_id"] != "bridge-once"
        assert set(posted) == ({"request_id", "content"} if kind == "text" else {"request_id"})
        assert (await bridge.client.app.state.services.adapter.get_dialog_status())["state"] == expected_state
        repeated = await controller.submit(**arguments)
        checked = await controller.receipt(user_id=MINI_USER, pet_id=MINI_PET, request_id="bridge-once")
        assert repeated == checked == receipt and len(bridge.posts) == 1
        public = json.dumps(receipt, ensure_ascii=False)
        assert all(secret not in public for secret in (
            SERIAL, bridge.pet, bridge.session, "synthetic-dialog-key", posted["request_id"], "请说一句你好",
        ))
        with sqlite3.connect(controller.path) as connection:
            saved = "\n".join(connection.iterdump())
        assert "请说一句你好" not in saved and bridge.session not in saved
    finally:
        controller.close()


@pytest.mark.asyncio
async def test_wrong_ack_after_actual_mock_delivery_latches_unknown_but_preserves_interrupt(
    tmp_path, dialog_bridge,
):
    bridge = dialog_bridge
    controller = _controller(tmp_path, bridge)
    try:
        bridge.corrupt_ack = True
        receipt = await controller.submit(user_id=MINI_USER, pet_id=MINI_PET,
            request_id="delivery-uncertain", kind="text", content="这个问题可能已经被接收",
            authorize=_authorize)
        assert receipt["status"] == "unknown"
        assert (await bridge.client.app.state.services.adapter.get_dialog_status())["state"] == "thinking"
        snapshot = await controller.snapshot(user_id=MINI_USER, pet_id=MINI_PET)
        assert snapshot["manualReviewRequired"] is True
        assert not snapshot["canListen"] and not snapshot["canSendText"] and snapshot["canInterrupt"]
        with pytest.raises(DialogControlError):
            await controller.submit(user_id=MINI_USER, pet_id=MINI_PET,
                request_id="never-resend", kind="listen", authorize=_authorize)
        assert len(bridge.posts) == 1
        bridge.corrupt_ack = False
        interrupted = await controller.submit(user_id=MINI_USER, pet_id=MINI_PET,
            request_id="independent-interrupt", kind="interrupt", authorize=_authorize)
        assert interrupted["status"] == "accepted" and len(bridge.posts) == 2
        assert (await controller.snapshot(user_id=MINI_USER, pet_id=MINI_PET))["manualReviewRequired"] is True
        original = await controller.receipt(user_id=MINI_USER, pet_id=MINI_PET, request_id="delivery-uncertain")
        assert original["status"] == "unknown"
    finally:
        controller.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", ["expired_session", "wrong_serial", "legacy_version", "disabled"])
async def test_real_ownership_version_and_disabled_gates_never_reach_command_api(
    tmp_path, dialog_bridge, problem,
):
    bridge = dialog_bridge
    if problem == "wrong_serial":
        bridge.serial = "DIFFERENT-SYNTHETIC-K1"
    if problem == "legacy_version":
        bridge.legacy = True
    controller = _controller(tmp_path, bridge, enabled=problem != "disabled",
        session="expired_synthetic_webpage_session" if problem == "expired_session" else None)
    try:
        snapshot = await controller.snapshot(user_id=MINI_USER, pet_id=MINI_PET)
        assert not snapshot["canSendText"] and not snapshot["canListen"]
        with pytest.raises(DialogControlError):
            await controller.submit(user_id=MINI_USER, pet_id=MINI_PET,
                request_id="no-side-effect", kind="text", content="不能发送",
                authorize=_authorize)
        assert not bridge.posts
    finally:
        controller.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("escaped_json", [False, True])
async def test_whole_mini_route_accepts_500_hanzi_without_expanding_legacy_body_limits(
    tmp_path, dialog_bridge, escaped_json,
):
    bridge = dialog_bridge
    controller = _controller(tmp_path, bridge)
    store = SessionStore(tmp_path / "sessions.sqlite3")
    app_id = "wx1234567890abcdef"
    identity = store.issue_session(app_id, "synthetic-openid", 3600)
    store.grant_pet(identity.user_id, MINI_PET, "合成对话猫")
    with store._connect() as connection:
        connection.execute("INSERT INTO mg_meta VALUES(?,?)", (
            "device:" + MINI_PET, hashlib.sha256(SERIAL.encode()).hexdigest(),
        ))
    reader = LocalDialogReader(upstream_url="http://127.0.0.1:18000", api_key="synthetic-dialog-key",
        expected_serial=SERIAL, product_session=bridge.session, product_pet_id=bridge.pet,
        transport=httpx.MockTransport(bridge))
    app = create_app(MiniGatewayConfig(db_path=store.path, app_id=app_id, status_pet_id=MINI_PET),
        store=store, status_reader=type("StatusIdentity", (), {"expected_serial": SERIAL})(),
        dialog_reader=reader, dialog_controller=controller)
    headers = {"Authorization": "Bearer " + identity.access_token, "Content-Type": "application/json"}
    base = f"/v1/pets/{MINI_PET}/dialog"
    content = "问题" * 250
    raw = json.dumps({"requestId": "whole-route-text", "kind": "text", "content": content},
                     ensure_ascii=escaped_json).encode("utf-8")
    assert 1024 < len(raw) < 4096
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get(base + "/control")).status_code == 401
            unauthorized = await client.post(base + "/commands", content=raw)
            assert unauthorized.status_code == 401 and not bridge.posts
            legacy = await client.post("/v1/auth/wechat", headers=headers, content=b"x" * 1025)
            assert legacy.status_code == 413
            wrong = await client.post("/v1/pets/wrong/dialog/commands", headers=headers, content=raw)
            assert wrong.status_code == 404 and not bridge.posts
            response = await client.post(base + "/commands", headers=headers, content=raw)
            assert response.status_code == 200
            assert response.headers["Cache-Control"] == "no-store"
            assert response.json()["data"]["status"] == "accepted"
            assert len(bridge.posts) == 1 and json.loads(bridge.posts[0].content)["content"] == content
            receipt = await client.get(base + "/requests/whole-route-text", headers=headers)
            assert receipt.status_code == 200 and receipt.json()["data"]["status"] == "accepted"
            assert len(bridge.posts) == 1 and content not in receipt.text
    finally:
        reader.close()
        controller.close()
