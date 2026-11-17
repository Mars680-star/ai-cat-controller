"""Bridge contract against the real product API, with no network or hardware.

The only unlock mutations below affect fresh synthetic pets in pytest's temporary
SQLite database. They never alter a real device, account, or personality level.
Only health/physical identity are simulated; product ownership, catalog, execution
receipts and durable product history come from the real application services.
"""

from __future__ import annotations

import json
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from ai_cat_controller.adapters.mock import MockAiCatAdapter
from ai_cat_controller.domain.actions import ACTIONS
from ai_cat_controller.core.errors import ActionConflictError
from ai_cat_controller.mini_gateway.motion import (
    LocalMotionController,
    MotionBusy,
    MotionDisabled,
    MotionLocked,
    MotionProductAuthRequired,
)
from ai_cat_controller.main import create_app


SERIAL = "K1-BRIDGE-TEST-001"
MINI_USER = "usr_synthetic_mini"
MINI_PET = "pet_synthetic_mini"
ACTION_IDS = tuple(action.action_id for action in ACTIONS)


def _login(client, code):
    response = client.post(
        "/api/v1/auth/mock-login",
        json={"login_code": code, "nickname": "合成桥接测试用户"},
    )
    assert response.status_code == 200
    return response.json()["data"]["session_token"]


class ProductBridge:
    def __init__(self, client, session, pet_id):
        self.client = client
        self.session = session
        self.pet_id = pet_id
        self.requests = []

    @property
    def headers(self):
        return {"X-Mock-Session": self.session}

    @property
    def posts(self):
        return [request for request in self.requests if request.method == "POST"]

    async def __call__(self, request):
        self.requests.append(request)
        path = request.url.path
        if path == "/health":
            assert "X-Mock-Session" not in request.headers
            return httpx.Response(200, json={"success": True, "data": {
                "status": "ok", "adapter": "local_k1",
            }})
        if path == "/api/v1/device/status":
            assert "X-Mock-Session" not in request.headers
            # Observe real Mock-backed scheduling while simulating only K1 identity.
            response = self.client.get(path)
            assert response.status_code == 200
            payload = response.json()
            payload["data"].update(
                adapter_mode="local_k1", connected=True, device_serial=SERIAL,
            )
            return httpx.Response(200, json=payload)
        if path == "/api/v1/motion/stop":
            assert "X-Mock-Session" not in request.headers
            response = self.client.post(path, json={})
            return httpx.Response(response.status_code, json=response.json())
        assert path == "/api/v1/pets" or path.startswith("/api/v1/pets/")
        headers = {"X-Mock-Session": request.headers["X-Mock-Session"]}
        arguments = {"json": json.loads(request.content)} if request.method == "POST" else {}
        response = self.client.request(request.method, path, headers=headers, **arguments)
        return httpx.Response(response.status_code, json=response.json())


@pytest.fixture
def product_bridge(client, settings):
    return _prepare_product_bridge(client, settings)


def _prepare_product_bridge(client, settings):
    assert isinstance(client.app.state.services.adapter, MockAiCatAdapter)
    session = _login(client, "synthetic-product-bridge-owner")
    response = client.post(
        "/api/v1/pets/bind", headers={"X-Mock-Session": session},
        json={"device_serial": SERIAL, "pet_name": "合成测试猫", "network_name": "Synthetic"},
    )
    assert response.status_code == 200
    pet_id = response.json()["data"]["pet"]["pet_id"]
    # Synthetic fixture preparation only; no real account/device unlock writes.
    with sqlite3.connect(settings.data_path) as connection:
        connection.execute(
            "UPDATE pets SET personality_id=?, intimacy_points=? WHERE pet_id=?",
            ("calm_guardian", 100, pet_id),
        )
    return ProductBridge(client, session, pet_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("action_id", ["head_nod", "head_shake", "quiet_companion"])
async def test_no_tail_three_catalog_bridges_real_product_filters_without_tail_metadata_guessing(
    tmp_path, settings, action_id,
):
    settings = settings.model_copy(update={"has_tail": False})
    with TestClient(create_app(settings)) as client:
        bridge = _prepare_product_bridge(client, settings)
        controller = _controller(tmp_path, bridge)
        try:
            snapshot = await controller.snapshot(user_id=MINI_USER, pet_id=MINI_PET)
            assert snapshot["hasTail"] is False and snapshot["deviceMotionEnabled"] is True
            assert {item["actionId"] for item in snapshot["actions"]} == {"head_nod", "head_shake", "quiet_companion"}
            assert all(item["available"] for item in snapshot["actions"])
            result = await controller.execute(MINI_USER, MINI_PET, action_id, "once", authorize=_authorize)
            assert result["execution"]["actionId"] == action_id and len(bridge.posts) == 1
            assert "/actions/" in bridge.posts[0].url.path
        finally:
            controller.close()


@pytest.mark.asyncio
async def test_paused_no_tail_real_product_bridge_never_submits_execute_but_still_stops(tmp_path, settings):
    settings = settings.model_copy(update={"has_tail": False, "enable_motion": False})
    with TestClient(create_app(settings)) as client:
        bridge = _prepare_product_bridge(client, settings)
        controller = _controller(tmp_path, bridge)
        try:
            snapshot = await controller.snapshot(user_id=MINI_USER, pet_id=MINI_PET)
            assert snapshot["deviceMotionEnabled"] is False and len(snapshot["actions"]) == 3
            assert not any(item["available"] for item in snapshot["actions"])
            with pytest.raises(MotionDisabled):
                await controller.execute(MINI_USER, MINI_PET, "head_nod", "blocked", authorize=_authorize)
            assert not bridge.posts
            stopped = await controller.stop(MINI_USER, MINI_PET, "stop", authorize=_authorize)
            assert stopped["execution"]["status"] == "stopped"
            assert [post.url.path for post in bridge.posts] == ["/api/v1/motion/stop"]
        finally:
            controller.close()


def _controller(tmp_path, bridge, *, session=None):
    return LocalMotionController(
        db_path=tmp_path / "private-motion-bridge" / "motion.sqlite3",
        enabled=True, upstream_url="http://127.0.0.1:18000",
        api_key="synthetic-test-key", expected_serial=SERIAL,
        product_session=bridge.session if session is None else session,
        product_pet_id=bridge.pet_id, transport=httpx.MockTransport(bridge),
    )


async def _authorize():
    return None


@pytest.mark.asyncio
@pytest.mark.parametrize("action_id", ACTION_IDS)
async def test_all_seven_actions_use_real_product_contract_and_durable_deduplication(
    tmp_path, product_bridge, action_id,
):
    bridge = product_bridge
    controller = _controller(tmp_path, bridge)
    try:
        snapshot = await controller.snapshot(user_id=MINI_USER, pet_id=MINI_PET)
        assert snapshot["verified"] is True
        assert {item["actionId"] for item in snapshot["actions"] if item["available"]} == set(ACTION_IDS)

        result = await controller.execute(
            MINI_USER, MINI_PET, action_id, "synthetic-one-command", authorize=_authorize,
        )
        assert result["execution"]["actionId"] == action_id
        assert result["execution"]["status"] in {"running", "ended_unverified"}
        assert len(bridge.posts) == 1
        request = bridge.posts[0]
        assert request.url.path == f"/api/v1/pets/{bridge.pet_id}/actions/{action_id}/execute"
        body = json.loads(request.content)
        assert set(body) == {"request_id"}
        assert body["request_id"] != "synthetic-one-command"
        assert len(body["request_id"]) <= 80

        # Gateway history must be a local read, not a second product submission.
        before = len(bridge.requests)
        history = await controller.history(user_id=MINI_USER, pet_id=MINI_PET)
        assert len(bridge.requests) == before
        assert len(history["items"]) == 1
        assert history["items"][0]["actionId"] == action_id

        replay = await controller.execute(
            MINI_USER, MINI_PET, action_id, "synthetic-one-command", authorize=_authorize,
        )
        assert replay["execution"]["executionId"] == result["execution"]["executionId"]
        assert len(bridge.posts) == 1
        records = bridge.client.get(
            f"/api/v1/pets/{bridge.pet_id}/actions/executions", headers=bridge.headers,
        )
        assert records.status_code == 200
        rows = records.json()["data"]
        assert len(rows) == 1
        assert rows[0]["action_id"] == action_id
        assert rows[0]["request_id"] == body["request_id"]
    finally:
        controller.close()


@pytest.mark.asyncio
async def test_autonomous_motion_race_has_durable_failed_product_receipt_not_unknown_latch(
    tmp_path, product_bridge, monkeypatch,
):
    bridge = product_bridge
    scheduler = bridge.client.app.state.services.motion
    original = scheduler._start

    async def contended_start(**kwargs):
        # Trigger only after every gateway preflight GET succeeded, at the
        # genuine scheduler submission boundary; no synthetic hardware runs.
        if kwargs["action_name"] == "tail_wag":
            raise ActionConflictError("已有动作正在执行: head_nod")
        return await original(**kwargs)

    monkeypatch.setattr(scheduler, "_start", contended_start)
    controller = _controller(tmp_path, bridge)
    try:
        with pytest.raises(MotionBusy):
            await controller.execute(MINI_USER, MINI_PET, "tail_wag", "autonomous-race", authorize=_authorize)
        history = await controller.history(user_id=MINI_USER, pet_id=MINI_PET)
        assert history["items"][0]["status"] == "failed"
        records = bridge.client.get(f"/api/v1/pets/{bridge.pet_id}/actions/executions", headers=bridge.headers)
        record = records.json()["data"][0]
        assert record["action_id"] == "tail_wag" and record["status"] == "failed"
        assert record["error"] == "已有动作正在执行: head_nod"
        assert scheduler.current_action == "idle"
        assert len(bridge.posts) == 1 and "/tail_wag/execute" in bridge.posts[0].url.path
        with sqlite3.connect(controller.path) as connection:
            assert connection.execute("SELECT value FROM mm_meta WHERE key='manual_review'").fetchone()[0] == "0"
        replay = await controller.execute(MINI_USER, MINI_PET, "tail_wag", "autonomous-race", authorize=_authorize)
        assert replay["execution"]["status"] == "failed" and len(bridge.posts) == 1
    finally:
        controller.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("credential", ["expired", "different-owner"])
async def test_real_product_session_rejection_never_falls_back_to_raw_heads(
    tmp_path, product_bridge, credential,
):
    bridge = product_bridge
    session = ("synthetic-expired-session-token" if credential == "expired" else
               _login(bridge.client, "synthetic-other-product-owner"))
    controller = _controller(tmp_path, bridge, session=session)
    try:
        snapshot = await controller.snapshot(user_id=MINI_USER, pet_id=MINI_PET)
        assert snapshot["verified"] is True
        assert not any(item["available"] for item in snapshot["actions"])
        with pytest.raises(MotionProductAuthRequired):
            await controller.execute(
                MINI_USER, MINI_PET, "head_nod", "synthetic-rejected-command", authorize=_authorize,
            )
        assert not bridge.posts
        assert (await controller.history(user_id=MINI_USER, pet_id=MINI_PET))["items"] == []
    finally:
        controller.close()


@pytest.mark.asyncio
async def test_real_product_level_lock_is_enforced_before_reserving_or_posting(
    tmp_path, product_bridge, settings,
):
    bridge = product_bridge
    # Lower only this temporary synthetic fixture, never actual device growth.
    with sqlite3.connect(settings.data_path) as connection:
        connection.execute("UPDATE pets SET intimacy_points=0 WHERE pet_id=?", (bridge.pet_id,))
    controller = _controller(tmp_path, bridge)
    try:
        snapshot = await controller.snapshot(user_id=MINI_USER, pet_id=MINI_PET)
        assert {item["actionId"] for item in snapshot["actions"] if item["available"]} == {"head_nod", "head_shake"}
        with pytest.raises(MotionLocked):
            await controller.execute(
                MINI_USER, MINI_PET, "celebration_combo", "synthetic-locked-command", authorize=_authorize,
            )
        assert not bridge.posts
        assert (await controller.history(user_id=MINI_USER, pet_id=MINI_PET))["items"] == []
    finally:
        controller.close()
