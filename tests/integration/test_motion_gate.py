from fastapi.testclient import TestClient
import pytest

from ai_cat_controller.adapters.command_runner import CommandResult
from ai_cat_controller.adapters.local_k1 import LocalK1Adapter
from ai_cat_controller.core.config import Settings
from ai_cat_controller.main import create_app


@pytest.mark.parametrize("has_tail", [True, False])
def test_disabled_local_motion_is_rejected_before_execution_or_history(
    tmp_path, monkeypatch, has_tail,
) -> None:
    calls = []

    class NoMovementRunner:
        async def run(self, executable, args):
            calls.append((executable, tuple(args)))
            if executable == "/usr/bin/ai-toy_app":
                assert args == ["motor", "stop"], "movement must never reach runner"
                return CommandResult(0, "no active motor process", "", False)
            return CommandResult(1, "inactive", "", False)

    serial_path = tmp_path / "serial-number"
    serial_path.write_text("K1-MOTION-GATE", encoding="ascii")
    settings = Settings(
        hardware_driver="local_k1",
        api_key_enabled=True,
        api_key="motion-gate-test-key",
        enable_motion=False,
        has_tail=has_tail,
        enable_tail_motion=True,
        motion_profile="k1_vendor_smooth",
        debug_unlock_all_actions=True,
        enable_touch_motion=True,
        enable_level_up_celebration=True,
        data_path=tmp_path / "product-data.db",
        dialog_config_path=tmp_path / "dialog-config.json",
        dialog_event_path=tmp_path / "dialog-events.jsonl",
        device_serial_path=serial_path,
        personality_runtime_path=tmp_path / "personality-runtime.json",
        touch_event_log_path=tmp_path / "touch.log",
        battery_supply_path=tmp_path / "battery",
        charger_supply_path=tmp_path / "charger",
        dialog_status_path=tmp_path / "dialog-status.json",
    )
    def inert_adapter_factory(resolved_settings, *, operation_gate):
        return LocalK1Adapter(resolved_settings, NoMovementRunner(), operation_gate=operation_gate)
    monkeypatch.setattr("ai_cat_controller.main.create_adapter", inert_adapter_factory)

    with TestClient(create_app(settings)) as client:
        assert client.app.state.services.adapter._operation_gate is client.app.state.maintenance_gate
        api_headers = {"X-API-Key": "motion-gate-test-key"}
        login = client.post(
            "/api/v1/auth/mock-login", headers=api_headers,
            json={"login_code": "motion-gate-user", "nickname": "测试用户"},
        )
        assert login.status_code == 200
        headers = {
            **api_headers,
            "X-Mock-Session": login.json()["data"]["session_token"],
        }
        bound = client.post(
            "/api/v1/pets/bind", headers=headers,
            json={"device_serial": "K1-MOTION-GATE", "pet_name": "小安"},
        )
        assert bound.status_code == 200
        pet_id = bound.json()["data"]["pet"]["pet_id"]

        actions = client.get(
            f"/api/v1/pets/{pet_id}/actions", headers=headers,
        ).json()["data"]
        assert len(actions) == (7 if has_tail else 3)
        if not has_tail:
            assert {action["action_id"] for action in actions} == {
                "head_shake", "head_nod", "quiet_companion",
            }
            device = client.get("/api/v1/device/status", headers=api_headers).json()["data"]
            assert device["has_tail"] is False
            assert device["tail_state"] == "not_present"
            for action_id in ("tail_wag", "proud_pose", "greeting_combo", "celebration_combo"):
                rejected = client.post(
                    f"/api/v1/pets/{pet_id}/actions/{action_id}/execute",
                    headers=headers, json={"request_id": f"no-tail-{action_id}"},
                )
                assert rejected.status_code == 501
                assert "没有尾部机构" in rejected.json()["message"]
            dashboard = client.get(
                f"/api/v1/pets/{pet_id}/dashboard", headers=headers,
            ).json()["data"]
            assert set(dashboard["unlocked_action_ids"]) <= {"head_shake", "head_nod", "quiet_companion"}
            intimacy = client.get(
                f"/api/v1/pets/{pet_id}/intimacy", headers=headers,
            ).json()["data"]
            assert "基础尾部动作" not in intimacy["next_unlocks"]
            personalities = client.get("/api/v1/personalities", headers=api_headers).json()["data"]
            assert all("tail_wag" not in item["default_actions"] for item in personalities)
            assert all(
                not set(item["action_triggers"].values()) & {"proud_pose", "greeting_combo", "celebration_combo"}
                for item in personalities
            )
            spec = client.get(
                f"/api/v1/integrations/mijia/pets/{pet_id}/spec", headers=headers,
            ).json()["data"]
            assert len(spec["actions"]) == 5
            assert "tail_wag" not in {action["action_id"] for action in spec["actions"]}
            prepared = client.post(
                f"/api/v1/integrations/mijia/pets/{pet_id}/prepare", headers=headers,
                json={"request_id": "no-tail-mijia-prepare"},
            ).json()["data"]
            gateway_id = prepared["gateway_device_id"]
            gateway = f"/api/v1/gateway/mijia/v1/devices/{gateway_id}"
            snapshot = client.get(gateway + "/snapshot", headers=api_headers).json()["data"]
            assert "tail_wag" not in snapshot["available_action_ids"]
            rejected = client.post(
                gateway + "/actions/tail_wag", headers=api_headers,
                json={"request_id": "no-tail-gateway-wag"},
            )
            assert rejected.status_code == 501
        assert all(action["available"] is False for action in actions)
        assert all("动作已暂停" in action["unavailable_reason"] for action in actions)
        for action in actions:
            rejected = client.post(
                f"/api/v1/pets/{pet_id}/actions/{action['action_id']}/execute",
                headers=headers,
                json={"request_id": f"blocked-{action['action_id']}"},
            )
            assert rejected.status_code == 501

        for path in ("head/shake", "head/nod", "tail/wag"):
            rejected = client.post(
                f"/api/v1/motion/{path}", headers=api_headers,
                json={"intensity": 0.5, "duration_ms": 600},
            )
            assert rejected.status_code == 501

        history = client.get(
            f"/api/v1/pets/{pet_id}/actions/executions", headers=headers,
        ).json()["data"]
        assert history == []
        assert client.app.state.services.motion.current_action == "idle"
        assert not any(executable == "/usr/bin/ai-toy_app" for executable, _ in calls)
        assert client.get("/control").status_code == 200
        assert client.get("/health").status_code == 200
        assert client.post("/api/v1/motion/stop", headers=api_headers).status_code == 200
