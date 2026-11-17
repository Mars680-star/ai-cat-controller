"""Software-only safety policy: temporary product DB and MockAdapter."""

from functools import partial
import sqlite3

from fastapi.testclient import TestClient
import pytest

from ai_cat_controller.core.config import Settings
from ai_cat_controller.core.errors import AdapterNotImplementedError
from ai_cat_controller.domain.actions import ACTIONS, action_requires_tail
from ai_cat_controller.domain.personalities import PERSONALITY_BY_ID
from ai_cat_controller.main import create_app


@pytest.fixture
def app_context(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "ai_cat_controller.services.product_mock_service.secrets.choice",
        lambda values: PERSONALITY_BY_ID["proud_star"],
    )

    def create(*, enabled, has_tail):
        settings = Settings(
            data_path=tmp_path / f"product-{enabled}-{has_tail}.db",
            dialog_config_path=tmp_path / "dialog.json",
            enable_motion=enabled, has_tail=has_tail,
            enable_tail_motion=True, debug_unlock_all_actions=True,
            enable_level_up_celebration=True,
        )
        return TestClient(create_app(settings)), settings
    return create


def bind(client):
    login = client.post("/api/v1/auth/mock-login", json={"login_code": "safety-user", "nickname": "测试用户"}).json()["data"]
    headers = {"X-Mock-Session": login["session_token"]}
    result = client.post("/api/v1/pets/bind", headers=headers,
                         json={"device_serial": "MOCK-SAFETY-POLICY", "pet_name": "小安"})
    assert result.status_code == 200
    return headers, result.json()["data"]["pet"]["pet_id"], login["user"]["user_id"]


def test_fixed_components_determine_tail_dependency_without_shortening_presets():
    assert {item.action_id for item in ACTIONS if action_requires_tail(item)} == {
        "tail_wag", "proud_pose", "greeting_combo", "celebration_combo",
    }


def test_paused_physical_touch_still_records_interaction_without_any_motion(app_context):
    context, _ = app_context(enabled=False, has_tail=True)
    with context as client:
        headers, pet_id, _ = bind(client)
        product = client.app.state.services.product
        assert client.portal is not None
        event = client.portal.call(partial(
            product.add_device_touch, device_serial="MOCK-SAFETY-POLICY",
            request_id="paused-physical-head-touch",
            metadata={"sensor": "head", "source": "test_physical_touch", "gesture": "short"},
        ))
        assert event["event_type"] == "touch" and event["duplicate"] is False
        assert event["touch_action"]["status"] == "skipped"
        assert event["touch_action"]["reason"] == "motion_disabled"
        assert not product._level_up_celebration_tasks
        assert client.app.state.services.motion._task is None
        assert client.get(f"/api/v1/pets/{pet_id}/actions/executions", headers=headers).json()["data"] == []


@pytest.mark.parametrize("has_tail", [True, False])
def test_pause_blocks_all_product_and_system_triggers_before_history_but_not_dialog(app_context, has_tail):
    context, _ = app_context(enabled=False, has_tail=has_tail)
    with context as client:
        headers, pet_id, owner = bind(client)
        product = client.app.state.services.product
        catalog = client.get(f"/api/v1/pets/{pet_id}/actions", headers=headers).json()["data"]
        assert len(catalog) == (7 if has_tail else 3)
        assert all(item["available"] is False for item in catalog)
        assert all("动作已暂停" in item["unavailable_reason"] for item in catalog)
        for action in ACTIONS:
            response = client.post(f"/api/v1/pets/{pet_id}/actions/{action.action_id}/execute",
                                   headers=headers, json={"request_id": "blocked-" + action.action_id})
            assert response.status_code == 501
            assert client.portal is not None
            with pytest.raises(AdapterNotImplementedError):
                client.portal.call(partial(product.execute_action, user_id=owner, pet_id=pet_id,
                                          action_id=action.action_id, request_id="system-" + action.action_id,
                                          system_trigger=True))
        runtime = client.get("/api/v1/personality/runtime").json()["data"]
        assert runtime["allowed_functions"] == []
        assert client.get(f"/api/v1/pets/{pet_id}/actions/executions", headers=headers).json()["data"] == []
        assert client.post("/api/v1/motion/stop").status_code == 200
        assert client.post("/api/v1/dialog/text", json={"content": "你好", "request_id": "speech-not-motion"}).status_code == 200


@pytest.mark.parametrize("enabled,has_tail", [(False, True), (False, False), (True, False)])
def test_level_up_does_not_schedule_an_impossible_celebration(app_context, enabled, has_tail):
    context, settings = app_context(enabled=enabled, has_tail=has_tail)
    with context as client:
        headers, pet_id, _ = bind(client)
        with sqlite3.connect(settings.data_path) as connection:
            connection.execute("UPDATE pets SET intimacy_points=19 WHERE pet_id=?", (pet_id,))
        result = client.post(f"/api/v1/pets/{pet_id}/interactions", headers=headers,
                             json={"event_type": "completed_task", "request_id": "safe-level-up"})
        assert result.status_code == 200
        event = result.json()["data"]
        assert event["level_up"]["celebration_scheduled"] is False
        assert not client.app.state.services.product._level_up_celebration_tasks
        assert client.get(f"/api/v1/pets/{pet_id}/actions/executions", headers=headers).json()["data"] == []


def test_tailless_device_has_no_impossible_promises_and_keeps_head_execution(app_context):
    context, settings = app_context(enabled=True, has_tail=False)
    with context as client:
        headers, pet_id, _ = bind(client)
        with sqlite3.connect(settings.data_path) as connection:
            connection.execute("UPDATE pets SET intimacy_points=20 WHERE pet_id=?", (pet_id,))
        dashboard = client.get(f"/api/v1/pets/{pet_id}/dashboard", headers=headers).json()["data"]
        assert set(dashboard["unlocked_action_ids"]) == {"head_shake", "head_nod", "quiet_companion"}
        assert "基础尾部动作" not in dashboard["intimacy"]["level"]["unlocks"]
        intimacy = client.get(f"/api/v1/pets/{pet_id}/intimacy", headers=headers).json()["data"]
        assert "基础尾部动作" not in intimacy["current_unlocks"]
        assert "组合动作" not in intimacy["next_unlocks"]
        response = client.post(f"/api/v1/pets/{pet_id}/actions/head_nod/execute", headers=headers,
                               json={"request_id": "allowed-head"})
        assert response.status_code == 202
        assert client.post("/api/v1/motion/stop").status_code == 200
        history = client.get(f"/api/v1/pets/{pet_id}/actions/executions", headers=headers).json()["data"]
        assert len(history) == 1 and history[0]["action_id"] == "head_nod"


@pytest.mark.parametrize("enabled,has_tail", [(False, True), (False, False), (True, False)])
def test_mijia_snapshot_uses_effective_motion_availability_and_stop_remains(app_context, enabled, has_tail):
    context, _ = app_context(enabled=enabled, has_tail=has_tail)
    with context as client:
        headers, pet_id, _ = bind(client)
        prepared = client.post(f"/api/v1/integrations/mijia/pets/{pet_id}/prepare", headers=headers,
                               json={"request_id": "prepare-safe"}).json()["data"]
        base = "/api/v1/gateway/mijia/v1/devices/" + prepared["gateway_device_id"]
        snapshot = client.get(base + "/snapshot").json()["data"]
        available = set(snapshot["available_action_ids"])
        assert {"stop_motion", "start_listening", "interrupt_dialog"} <= available
        assert "tail_wag" not in available
        if not enabled:
            assert not {"head_nod", "head_shake"} & available
        assert client.post(base + "/actions/stop_motion", json={"request_id": "safe-stop"}).status_code == 202
