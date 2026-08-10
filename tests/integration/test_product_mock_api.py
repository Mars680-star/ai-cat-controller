import json
import sqlite3
import time
from functools import partial
from pathlib import Path

from fastapi.testclient import TestClient

from ai_cat_controller.core.config import Settings
from ai_cat_controller.domain.personalities import PERSONALITY_BY_ID
from ai_cat_controller.local_speech import LocalPhrasePlayer
from ai_cat_controller.main import create_app


def _login(
    client: TestClient,
    *,
    code: str = "wx-test-user",
    nickname: str = "测试用户",
) -> tuple[dict[str, str], dict]:
    response = client.post(
        "/api/v1/auth/mock-login",
        json={"login_code": code, "nickname": nickname},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    return {"X-Mock-Session": data["session_token"]}, data


def _bind(
    client: TestClient,
    headers: dict[str, str],
    *,
    serial: str = "K1-MOCK-0001",
) -> dict:
    response = client.post(
        "/api/v1/pets/bind",
        headers=headers,
        json={
            "device_serial": serial,
            "pet_name": "小安",
            "network_name": "Lab Wi-Fi",
        },
    )
    assert response.status_code == 200
    return response.json()["data"]


def _add_device_touch(
    client: TestClient,
    *,
    serial: str,
    request_id: str,
    sensor: str,
) -> dict:
    services = client.app.state.services
    assert client.portal is not None
    return client.portal.call(
        partial(
            services.product.add_device_touch,
            device_serial=serial,
            request_id=request_id,
            metadata={
                "source": "test_touch",
                "sensor": sensor,
                "gesture": "short",
            },
        )
    )


def test_login_bind_blind_box_and_dashboard(client: TestClient) -> None:
    headers, login = _login(client)
    bound = _bind(client, headers)
    pet_id = bound["pet"]["pet_id"]

    personalities = client.get("/api/v1/personalities").json()["data"]
    dashboard = client.get(
        f"/api/v1/pets/{pet_id}/dashboard", headers=headers
    )

    assert len(personalities) == 5
    assert bound["blind_box_revealed"] is True
    assert login["user"]["user_id"].startswith("usr_")
    assert bound["pet"]["device_id"].startswith("dev_")
    assert pet_id.startswith("pet_")
    assert len({login["user"]["user_id"], bound["pet"]["device_id"], pet_id}) == 3
    assert dashboard.status_code == 200
    assert dashboard.json()["data"]["pet"]["serial_number"] == "K1-MOCK-0001"
    assert dashboard.json()["data"]["intimacy"]["level"]["level"] == 0
    runtime = client.get("/api/v1/personality/runtime").json()["data"]
    assert runtime["active"] is True
    assert runtime["sync_state"] == "mock_only"
    assert runtime["personality_id"] == bound["pet"]["personality_id"]
    assert runtime["native_applied"] is False


def test_session_and_owner_isolation(client: TestClient) -> None:
    missing_session = client.get("/api/v1/pets")
    assert missing_session.status_code == 401

    first_headers, _ = _login(client, code="owner-a")
    _bind(client, first_headers, serial="K1-OWNED")
    second_headers, _ = _login(client, code="owner-b")
    conflict = client.post(
        "/api/v1/pets/bind",
        headers=second_headers,
        json={
            "device_serial": "K1-OWNED",
            "pet_name": "另一只猫",
            "network_name": "Other Wi-Fi",
        },
    )
    assert conflict.status_code == 409


def test_intimacy_idempotency_daily_cap_and_unlocks(client: TestClient) -> None:
    headers, _ = _login(client)
    pet_id = _bind(client, headers)["pet"]["pet_id"]

    first = client.post(
        f"/api/v1/pets/{pet_id}/interactions",
        headers=headers,
        json={"event_type": "completed_task", "request_id": "same-request"},
    ).json()["data"]
    duplicate = client.post(
        f"/api/v1/pets/{pet_id}/interactions",
        headers=headers,
        json={"event_type": "completed_task", "request_id": "same-request"},
    ).json()["data"]
    assert first["points_after"] == 5
    assert duplicate["duplicate"] is True
    assert duplicate["points_after"] == 5

    for index in range(1, 10):
        client.post(
            f"/api/v1/pets/{pet_id}/interactions",
            headers=headers,
            json={
                "event_type": "touch",
                "request_id": f"touch-{index}",
            },
        )
    for index in range(1, 8):
        client.post(
            f"/api/v1/pets/{pet_id}/interactions",
            headers=headers,
            json={
                "event_type": "valid_dialog",
                "request_id": f"dialog-{index}",
            },
        )

    intimacy = client.get(
        f"/api/v1/pets/{pet_id}/intimacy", headers=headers
    ).json()["data"]
    assert intimacy["points"] == 23
    assert intimacy["level"]["level"] == 1
    assert intimacy["daily_growth_cap"] == 50
    assert any(event["points_delta"] == 0 for event in intimacy["history"])


def test_growth_api_reuses_existing_interactions_and_dialogs(client: TestClient) -> None:
    headers, _ = _login(client, code="growth-events-user")
    pet_id = _bind(
        client,
        headers,
        serial="K1-GROWTH-EVENTS",
    )["pet"]["pet_id"]

    initial = client.get(
        f"/api/v1/pets/{pet_id}/growth",
        headers=headers,
    ).json()["data"]
    assert initial["debug_values_visible"] is True
    assert all(attribute["value"] == 0 for attribute in initial["attributes"].values())

    first_task = client.post(
        f"/api/v1/pets/{pet_id}/interactions",
        headers=headers,
        json={"event_type": "completed_task", "request_id": "growth-task"},
    ).json()["data"]
    duplicate_task = client.post(
        f"/api/v1/pets/{pet_id}/interactions",
        headers=headers,
        json={"event_type": "completed_task", "request_id": "growth-task"},
    ).json()["data"]
    daily_meeting = client.post(
        f"/api/v1/pets/{pet_id}/interactions",
        headers=headers,
        json={"event_type": "daily_check_in", "request_id": "growth-daily"},
    ).json()["data"]
    dialog = client.post(
        f"/api/v1/pets/{pet_id}/dialogs",
        headers=headers,
        json={"content": "机器人传感器的原理是什么？", "trigger_action": False},
    ).json()["data"]

    assert first_task["growth"]["duplicate"] is False
    assert duplicate_task["growth"]["duplicate"] is True
    assert daily_meeting["growth"]["duplicate"] is False
    assert dialog["growth_event"]["duplicate"] is False
    growth = client.get(
        f"/api/v1/pets/{pet_id}/growth",
        headers=headers,
    ).json()["data"]
    event_types = {event["event_type"] for event in growth["recent_events"]}
    assert {
        "completed_task",
        "daily_meeting",
        "knowledge_discussion",
    } <= event_types
    assert growth["attributes"]["discipline"]["value"] > 0
    assert growth["attributes"]["knowledge"]["value"] > 0


def test_debug_growth_reaches_tags_and_updates_runtime(client: TestClient) -> None:
    headers, _ = _login(client, code="growth-debug-user")
    pet_id = _bind(
        client,
        headers,
        serial="K1-GROWTH-DEBUG",
    )["pet"]["pet_id"]

    response = client.post(
        f"/api/v1/pets/{pet_id}/growth/debug",
        headers=headers,
        json={
            "request_id": "knowledge-batch",
            "event_type": "knowledge_discussion",
            "count": 220,
            "topic": "robotics",
            "emotion": "neutral",
            "engagement": 1.0,
        },
    )

    assert response.status_code == 200
    result = response.json()["data"]
    assert result["batch"]["processed"] == 220
    active_ids = {tag["tag_id"] for tag in result["growth"]["active_tags"]}
    assert {"explorer", "scholar_cat"} <= active_ids
    tag_history = {
        tag["tag_id"]: tag["status"]
        for tag in result["growth"]["tag_history"]
    }
    assert tag_history["little_explorer"] == "replaced"
    assert tag_history["little_scholar"] == "replaced"
    runtime = client.get("/api/v1/personality/runtime").json()["data"]
    assert {"explorer", "scholar_cat"} <= set(runtime["growth_tags"])
    assert runtime["behavior_directives"]


def test_debug_growth_batch_is_idempotent(client: TestClient) -> None:
    headers, _ = _login(client, code="growth-debug-idempotent")
    pet_id = _bind(
        client,
        headers,
        serial="K1-GROWTH-IDEMPOTENT",
    )["pet"]["pet_id"]
    request = {
        "request_id": "same-growth-batch",
        "event_type": "question",
        "count": 3,
        "topic": "science",
        "emotion": "neutral",
        "engagement": 1.0,
    }

    first = client.post(
        f"/api/v1/pets/{pet_id}/growth/debug",
        headers=headers,
        json=request,
    ).json()["data"]["batch"]
    second = client.post(
        f"/api/v1/pets/{pet_id}/growth/debug",
        headers=headers,
        json=request,
    ).json()["data"]["batch"]

    assert first["processed"] == 3
    assert second["processed"] == 0
    assert second["duplicates"] == 3


def test_growth_follows_pet_but_previous_owner_events_are_private(
    client: TestClient,
) -> None:
    first_headers, _ = _login(client, code="growth-owner-a")
    pet_id = _bind(
        client,
        first_headers,
        serial="K1-GROWTH-TRANSFER",
    )["pet"]["pet_id"]
    client.post(
        f"/api/v1/pets/{pet_id}/interactions",
        headers=first_headers,
        json={
            "event_type": "touch",
            "request_id": "private-touch",
            "metadata": {"sensor": "head"},
        },
    )
    before = client.get(
        f"/api/v1/pets/{pet_id}/growth",
        headers=first_headers,
    ).json()["data"]
    assert before["recent_events"]
    client.post(f"/api/v1/pets/{pet_id}/unbind", headers=first_headers)

    second_headers, _ = _login(client, code="growth-owner-b")
    rebound = _bind(client, second_headers, serial="K1-GROWTH-TRANSFER")
    assert rebound["pet"]["pet_id"] == pet_id
    after = client.get(
        f"/api/v1/pets/{pet_id}/growth",
        headers=second_headers,
    ).json()["data"]

    assert after["attributes"]["empathy"]["value"] > 0
    assert after["recent_events"] == []


def test_local_k1_growth_debug_is_disabled_by_default(tmp_path) -> None:
    serial_path = tmp_path / "serial-number"
    serial_path.write_text("K1-GROWTH-LOCAL", encoding="ascii")
    settings = Settings(
        hardware_driver="local_k1",
        api_key_enabled=True,
        api_key="growth-test-key",
        enable_touch_motion=False,
        data_path=tmp_path / "local-growth.db",
        dialog_config_path=tmp_path / "dialog-config.json",
        dialog_event_path=tmp_path / "dialog-events.jsonl",
        device_serial_path=serial_path,
        personality_runtime_path=tmp_path / "personality-runtime.json",
        touch_event_log_path=tmp_path / "touch.log",
    )
    with TestClient(create_app(settings)) as test_client:
        api_headers = {"X-API-Key": "growth-test-key"}
        login = test_client.post(
            "/api/v1/auth/mock-login",
            headers=api_headers,
            json={"login_code": "local-growth-user", "nickname": "测试用户"},
        ).json()["data"]
        headers = {
            **api_headers,
            "X-Mock-Session": login["session_token"],
        }
        pet_id = _bind(
            test_client,
            headers,
            serial="K1-GROWTH-LOCAL",
        )["pet"]["pet_id"]
        test_client.post(
            f"/api/v1/pets/{pet_id}/interactions",
            headers=headers,
            json={"event_type": "completed_task", "request_id": "local-task"},
        )

        state = test_client.get(
            f"/api/v1/pets/{pet_id}/growth",
            headers=headers,
        ).json()["data"]
        response = test_client.post(
            f"/api/v1/pets/{pet_id}/growth/debug",
            headers=headers,
            json={"event_type": "question", "count": 1},
        )

        assert state["debug_values_visible"] is False
        assert all("value" not in item for item in state["attributes"].values())
        assert all(
            "attribute_delta" not in event for event in state["recent_events"]
        )
        assert state["recent_events"][0]["changed_attributes"]
        assert all("evidence" not in tag for tag in state["tag_history"])
        assert response.status_code == 403
        assert response.json()["data"]["code"] == "operation_disabled"


def test_debug_unlimited_touch_bypasses_only_touch_limits(tmp_path) -> None:
    app = create_app(
        Settings(
            hardware_driver="mock",
            debug_unlimited_touch_intimacy=True,
            intimacy_daily_cap=3,
            enable_touch_motion=False,
            data_path=tmp_path / "unlimited-touch.db",
            dialog_config_path=tmp_path / "dialog-config.json",
        )
    )
    with TestClient(app) as test_client:
        headers, _ = _login(test_client, code="unlimited-touch-user")
        pet_id = _bind(
            test_client,
            headers,
            serial="K1-UNLIMITED-TOUCH",
        )["pet"]["pet_id"]

        events = []
        for index in range(10):
            events.append(
                test_client.post(
                    f"/api/v1/pets/{pet_id}/interactions",
                    headers=headers,
                    json={
                        "event_type": "touch",
                        "request_id": f"debug-touch-{index}",
                    },
                ).json()["data"]
            )
        duplicate = test_client.post(
            f"/api/v1/pets/{pet_id}/interactions",
            headers=headers,
            json={"event_type": "touch", "request_id": "debug-touch-0"},
        ).json()["data"]
        task = test_client.post(
            f"/api/v1/pets/{pet_id}/interactions",
            headers=headers,
            json={"event_type": "completed_task", "request_id": "limited-task"},
        ).json()["data"]
        intimacy = test_client.get(
            f"/api/v1/pets/{pet_id}/intimacy",
            headers=headers,
        ).json()["data"]

        assert all(event["points_delta"] == 1 for event in events)
        assert events[-1]["points_after"] == 10
        assert duplicate["duplicate"] is True
        assert duplicate["points_after"] == 1
        assert task["points_delta"] == 0
        assert intimacy["points"] == 10
        assert intimacy["debug_unlimited_touch_intimacy"] is True


def test_physical_touch_starts_safe_action_and_preserves_cooldown(
    client: TestClient,
) -> None:
    headers, _ = _login(client, code="touch-action-user")
    pet_id = _bind(client, headers, serial="K1-TOUCH-ACTION")["pet"]["pet_id"]

    first = _add_device_touch(
        client,
        serial="K1-TOUCH-ACTION",
        request_id="physical-touch-1",
        sensor="head",
    )
    assert first["points_delta"] == 1
    assert first["touch_action"]["status"] == "started"
    assert first["touch_action"]["action_id"] == "head_nod"

    services = client.app.state.services
    assert client.portal is not None
    client.portal.call(services.motion.wait_until_idle)
    second = _add_device_touch(
        client,
        serial="K1-TOUCH-ACTION",
        request_id="physical-touch-2",
        sensor="nose",
    )
    duplicate = _add_device_touch(
        client,
        serial="K1-TOUCH-ACTION",
        request_id="physical-touch-1",
        sensor="head",
    )

    assert second["points_delta"] == 1
    assert second["touch_action"] == {
        "status": "skipped",
        "reason": "touch_motion_cooldown",
    }
    assert duplicate["duplicate"] is True
    assert duplicate["touch_action"] == {
        "status": "skipped",
        "reason": "duplicate_touch",
    }
    executions = client.get(
        f"/api/v1/pets/{pet_id}/actions/executions",
        headers=headers,
    ).json()["data"]
    assert len(executions) == 1
    assert executions[0]["action_id"] == "head_nod"


def test_physical_touch_does_not_move_during_dialog(client: TestClient) -> None:
    headers, _ = _login(client, code="touch-dialog-user")
    pet_id = _bind(client, headers, serial="K1-TOUCH-DIALOG")["pet"]["pet_id"]
    services = client.app.state.services
    assert client.portal is not None
    client.portal.call(services.adapter.wake_dialog)

    event = _add_device_touch(
        client,
        serial="K1-TOUCH-DIALOG",
        request_id="dialog-touch-1",
        sensor="right_foot",
    )

    assert event["points_delta"] == 1
    assert event["touch_action"]["status"] == "skipped"
    assert event["touch_action"]["reason"] == "dialog_busy"
    executions = client.get(
        f"/api/v1/pets/{pet_id}/actions/executions",
        headers=headers,
    ).json()["data"]
    assert executions == []


def test_physical_touch_plays_local_phrase_after_motion(
    monkeypatch,
    tmp_path,
) -> None:
    played: list[str] = []

    def fake_play_touch(
        self: LocalPhrasePlayer,
        sensor: str,
    ) -> Path:
        del self
        played.append(sensor)
        return tmp_path / "touch.wav"

    monkeypatch.setattr(LocalPhrasePlayer, "play_touch", fake_play_touch)
    app = create_app(
        Settings(
            hardware_driver="mock",
            motion_cooldown_seconds=0.0,
            data_path=tmp_path / "touch-speech.db",
            dialog_config_path=tmp_path / "dialog-config.json",
            enable_touch_speech=True,
        )
    )
    with TestClient(app) as test_client:
        headers, _ = _login(test_client, code="touch-speech-user")
        _bind(test_client, headers, serial="K1-TOUCH-SPEECH")
        event = _add_device_touch(
            test_client,
            serial="K1-TOUCH-SPEECH",
            request_id="touch-speech-1",
            sensor="back",
        )
        assert event["touch_action"]["speech_scheduled"] is True
        deadline = time.monotonic() + 2.0
        while not played and time.monotonic() < deadline:
            time.sleep(0.01)

    assert played == ["back"]


def test_action_catalog_rejects_locked_and_tracks_idempotency(
    client: TestClient,
) -> None:
    headers, _ = _login(client)
    pet_id = _bind(client, headers)["pet"]["pet_id"]
    catalog = client.get(
        f"/api/v1/pets/{pet_id}/actions", headers=headers
    ).json()["data"]
    assert len(catalog) >= 7
    assert all(item["parameters_editable"] is False for item in catalog)
    assert all(item["available"] is True for item in catalog)

    locked = client.post(
        f"/api/v1/pets/{pet_id}/actions/celebration_combo/execute",
        headers=headers,
        json={"request_id": "locked-action"},
    )
    assert locked.status_code == 409

    first = client.post(
        f"/api/v1/pets/{pet_id}/actions/head_shake/execute",
        headers=headers,
        json={"request_id": "one-action"},
    )
    duplicate = client.post(
        f"/api/v1/pets/{pet_id}/actions/head_shake/execute",
        headers=headers,
        json={"request_id": "one-action"},
    )
    assert first.status_code == 202
    assert first.json()["data"]["status"] == "running"
    assert duplicate.status_code == 202
    assert duplicate.json()["data"]["duplicate"] is True

    deadline = time.monotonic() + 2.0
    execution = None
    while time.monotonic() < deadline:
        execution = client.get(
            f"/api/v1/pets/{pet_id}/actions/executions",
            headers=headers,
        ).json()["data"][0]
        if execution["status"] == "completed":
            break
        time.sleep(0.05)
    assert execution["status"] == "completed"


def test_debug_unlock_exposes_and_executes_all_safe_actions(tmp_path) -> None:
    app = create_app(
        Settings(
            hardware_driver="mock",
            debug_unlock_all_actions=True,
            motion_cooldown_seconds=0.0,
            data_path=tmp_path / "debug-actions.db",
            dialog_config_path=tmp_path / "dialog-config.json",
        )
    )
    with TestClient(app) as test_client:
        headers, _ = _login(test_client, code="debug-action-user")
        pet_id = _bind(test_client, headers, serial="K1-DEBUG-ACTIONS")["pet"][
            "pet_id"
        ]
        catalog = test_client.get(
            f"/api/v1/pets/{pet_id}/actions", headers=headers
        ).json()["data"]

        assert len(catalog) == 7
        assert all(action["unlocked"] for action in catalog)
        assert any(action["debug_unlocked"] for action in catalog)
        response = test_client.post(
            f"/api/v1/pets/{pet_id}/actions/celebration_combo/execute",
            headers=headers,
            json={"request_id": "debug-celebration"},
        )

        assert response.status_code == 202
        assert response.json()["data"]["status"] == "running"


def test_dialog_history_settings_device_status_and_feedback(
    client: TestClient,
) -> None:
    headers, _ = _login(client)
    pet_id = _bind(client, headers)["pet"]["pet_id"]
    dialog = client.post(
        f"/api/v1/pets/{pet_id}/dialogs",
        headers=headers,
        json={"content": "今天适合做什么？", "trigger_action": False},
    )
    assert dialog.status_code == 200
    dialog_data = dialog.json()["data"]
    assert dialog_data["voice_id"] in {
        personality.voice_id for personality in PERSONALITY_BY_ID.values()
    }
    assert dialog_data["voice_integration_status"] == "mock_only"

    history = client.get(
        f"/api/v1/pets/{pet_id}/dialogs", headers=headers
    ).json()["data"]
    assert [message["role"] for message in history] == ["assistant", "user"]

    conversations = client.get(
        f"/api/v1/pets/{pet_id}/dialog-conversations",
        headers=headers,
    ).json()["data"]
    assert conversations["sync"]["conversation_count"] == 1
    assert conversations["conversations"][0]["status"] == "complete"
    assert conversations["conversations"][0]["source"] == "mock"
    assert conversations["conversations"][0]["message_count"] == 2
    conversation_id = conversations["conversations"][0]["conversation_id"]
    detail = client.get(
        f"/api/v1/pets/{pet_id}/dialog-conversations/{conversation_id}",
        headers=headers,
    ).json()["data"]
    assert [message["role"] for message in detail["messages"]] == [
        "user",
        "assistant",
    ]

    settings = client.patch(
        f"/api/v1/pets/{pet_id}/settings",
        headers=headers,
        json={"name": "安心", "volume": 35},
    ).json()["data"]
    assert settings["name"] == "安心"
    assert settings["volume"] == 35
    device_status = client.get("/api/v1/device/status").json()["data"]
    assert device_status["output_volume_available"] is True
    assert device_status["output_volume_percent"] == 35
    assert device_status["output_muted"] is False

    offline = client.post(
        f"/api/v1/pets/{pet_id}/mock-device-status",
        headers=headers,
        json={
            "online": False,
            "battery_percent": 12,
            "charging": True,
            "network_status": "offline",
        },
    )
    assert offline.status_code == 200
    assert offline.json()["data"]["battery_percent"] == 12
    refused = client.post(
        f"/api/v1/pets/{pet_id}/actions/head_nod/execute",
        headers=headers,
        json={"request_id": "offline-action"},
    )
    assert refused.status_code == 503

    feedback = client.post(
        f"/api/v1/pets/{pet_id}/feedback",
        headers=headers,
        json={"category": "device", "content": "网络状态显示异常"},
    )
    assert feedback.status_code == 200
    assert feedback.json()["data"]["feedback_id"].startswith("fb_")


def test_product_data_reset_is_disabled_by_default(client: TestClient) -> None:
    headers, _ = _login(client, code="reset-disabled-user")

    response = client.post(
        "/api/v1/admin/reset-product-data",
        headers=headers,
        json={"confirmation": "RESET_PRODUCT_DATA"},
    )

    assert response.status_code == 403
    assert response.json()["data"]["code"] == "operation_disabled"


def test_product_data_reset_backs_up_and_clears_experience_data(
    tmp_path,
) -> None:
    data_path = tmp_path / "product-data.db"
    runtime_paths = {
        "personality_runtime_path": tmp_path / "personality-runtime.json",
        "dialog_event_path": tmp_path / "dialog-events.jsonl",
        "dialog_text_request_path": tmp_path / "dialog-text-request.json",
        "dialog_config_path": tmp_path / "dialog-runtime-config.json",
    }
    app = create_app(
        Settings(
            hardware_driver="mock",
            motion_cooldown_seconds=0.0,
            data_path=data_path,
            enable_product_data_reset=True,
            **runtime_paths,
        )
    )
    with TestClient(app) as test_client:
        headers, _ = _login(test_client, code="reset-enabled-user")
        pet_id = _bind(
            test_client,
            headers,
            serial="K1-RESET-ALL-DATA",
        )["pet"]["pet_id"]
        test_client.post(
            f"/api/v1/pets/{pet_id}/interactions",
            headers=headers,
            json={"event_type": "completed_task", "request_id": "reset-task"},
        )
        test_client.post(
            f"/api/v1/pets/{pet_id}/dialogs",
            headers=headers,
            json={"content": "格式化前的对话", "trigger_action": False},
        )
        test_client.post(
            f"/api/v1/pets/{pet_id}/feedback",
            headers=headers,
            json={"category": "other", "content": "格式化前的反馈"},
        )
        test_client.post(
            f"/api/v1/pets/{pet_id}/actions/head_shake/execute",
            headers=headers,
            json={"request_id": "reset-action"},
        )
        for path in runtime_paths.values():
            path.write_text('{"test":true}\n', encoding="utf-8")

        invalid = test_client.post(
            "/api/v1/admin/reset-product-data",
            headers=headers,
            json={"confirmation": "RESET"},
        )
        assert invalid.status_code == 422

        response = test_client.post(
            "/api/v1/admin/reset-product-data",
            headers=headers,
            json={"confirmation": "RESET_PRODUCT_DATA"},
        )

        assert response.status_code == 200
        result = response.json()["data"]
        assert result["reset"] is True
        assert result["backup_id"].startswith("product-data-reset-")
        assert "/" not in result["backup_id"]
        assert "backup_directory" not in result
        assert result["deleted"] == {
            "feedback": 1,
            "action_executions": 1,
            "dialog_history": 2,
            "growth_tags": 0,
            "growth_events": 2,
            "growth_attributes": 3,
            "interaction_events": 2,
            "pets": 1,
            "devices": 1,
            "users": 1,
        }
        assert set(result["archived_files"]) == {
            path.name for path in runtime_paths.values()
        }
        assert all(not path.exists() for path in runtime_paths.values())
        assert test_client.portal is not None
        personality_status = test_client.portal.call(
            test_client.app.state.services.personality.status
        )
        assert personality_status == {
            "active": False,
            "sync_state": "not_configured",
        }

        expired_session = test_client.get("/api/v1/pets", headers=headers)
        assert expired_session.status_code == 401

    backup_directory = tmp_path / "backups" / result["backup_id"]
    backup_database = backup_directory / "product-data.db"
    assert backup_database.exists()
    assert backup_database.stat().st_mode & 0o777 == 0o600
    assert all((backup_directory / path.name).exists() for path in runtime_paths.values())
    with sqlite3.connect(backup_database) as backup:
        assert backup.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
        assert backup.execute("SELECT COUNT(*) FROM pets").fetchone()[0] == 1
        assert backup.execute("SELECT COUNT(*) FROM dialog_history").fetchone()[0] == 2
        assert backup.execute("SELECT COUNT(*) FROM growth_events").fetchone()[0] == 2
    with sqlite3.connect(data_path) as active:
        for table in (
            "feedback",
            "action_executions",
            "dialog_history",
            "growth_tags",
            "growth_events",
            "growth_attributes",
            "interaction_events",
            "pets",
            "devices",
            "users",
        ):
            assert active.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_product_data_reset_rejects_active_dialog(tmp_path) -> None:
    app = create_app(
        Settings(
            hardware_driver="mock",
            data_path=tmp_path / "busy-reset.db",
            dialog_config_path=tmp_path / "dialog-config.json",
            enable_product_data_reset=True,
        )
    )
    with TestClient(app) as test_client:
        headers, _ = _login(test_client, code="busy-reset-user")
        _bind(test_client, headers, serial="K1-BUSY-RESET")
        assert test_client.portal is not None
        test_client.portal.call(test_client.app.state.services.adapter.wake_dialog)

        response = test_client.post(
            "/api/v1/admin/reset-product-data",
            headers=headers,
            json={"confirmation": "RESET_PRODUCT_DATA"},
        )

        assert response.status_code == 409
        assert response.json()["data"]["code"] == "action_conflict"
        assert len(test_client.get("/api/v1/pets", headers=headers).json()["data"]) == 1


def test_native_dialog_events_are_imported_once_for_bound_device(tmp_path) -> None:
    event_path = tmp_path / "dialog-events.jsonl"
    settings = Settings(
        hardware_driver="mock",
        motion_cooldown_seconds=0.0,
        data_path=tmp_path / "native-dialog.db",
        dialog_event_path=event_path,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        headers, _ = _login(client, code="native-dialog-user")
        pet_id = _bind(client, headers, serial="K1-NATIVE-HISTORY")["pet"]["pet_id"]
        created_at_ms = int(time.time() * 1000) + 100
        events = [
            {
                "event_id": "event-user-1",
                "conversation_id": "native-K1-NATIVE-HISTORY-1",
                "device_serial": "K1-NATIVE-HISTORY",
                "role": "user",
                "content": "今天星期几？",
                "created_at_ms": created_at_ms,
            },
            {
                "event_id": "event-assistant-1",
                "conversation_id": "native-K1-NATIVE-HISTORY-1",
                "device_serial": "K1-NATIVE-HISTORY",
                "role": "assistant",
                "content": "今天是星期四。",
                "created_at_ms": created_at_ms + 1,
            },
            {
                "event_id": "event-other-device",
                "conversation_id": "native-other-1",
                "device_serial": "OTHER-K1",
                "role": "user",
                "content": "不应导入",
                "created_at_ms": created_at_ms,
            },
        ]
        event_path.write_text(
            "\n".join(json.dumps(event, ensure_ascii=False) for event in events)
            + "\n{malformed}\n",
            encoding="utf-8",
        )

        first = client.get(
            f"/api/v1/pets/{pet_id}/dialogs",
            headers=headers,
        ).json()["data"]
        second = client.get(
            f"/api/v1/pets/{pet_id}/dialogs",
            headers=headers,
        ).json()["data"]

        assert [message["content"] for message in first] == [
            "今天是星期四。",
            "今天星期几？",
        ]
        assert second == first
        assert first[0]["voice_id"] == "volcengine_tts"


def test_native_dialog_events_are_imported_during_application_restart(
    tmp_path,
) -> None:
    event_path = tmp_path / "dialog-events.jsonl"
    database_path = tmp_path / "native-dialog-restart.db"
    settings = Settings(
        hardware_driver="mock",
        motion_cooldown_seconds=0.0,
        data_path=database_path,
        dialog_event_path=event_path,
    )
    with TestClient(create_app(settings)) as client:
        headers, login = _login(client, code="native-dialog-restart-user")
        pet_id = _bind(
            client,
            headers,
            serial="K1-NATIVE-RESTART",
        )["pet"]["pet_id"]
        user_id = login["user"]["user_id"]

    created_at_ms = int(time.time() * 1000) + 100
    event_path.write_text(
        "\n".join(
            json.dumps(event, ensure_ascii=False)
            for event in (
                {
                    "event_id": "event-restart-user",
                    "conversation_id": "native-K1-NATIVE-RESTART-1",
                    "device_serial": "K1-NATIVE-RESTART",
                    "role": "user",
                    "content": "重启后还能看到吗？",
                    "created_at_ms": created_at_ms,
                },
                {
                    "event_id": "event-restart-assistant",
                    "conversation_id": "native-K1-NATIVE-RESTART-1",
                    "device_serial": "K1-NATIVE-RESTART",
                    "role": "assistant",
                    "content": "可以，记录已经恢复。",
                    "created_at_ms": created_at_ms + 1,
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )

    with TestClient(create_app(settings)):
        with sqlite3.connect(database_path) as connection:
            rows = connection.execute(
                """
                SELECT role, content
                FROM dialog_history
                WHERE pet_id = ? AND user_id = ?
                ORDER BY created_at ASC
                """,
                (pet_id, user_id),
            ).fetchall()

    assert rows == [
        ("user", "重启后还能看到吗？"),
        ("assistant", "可以，记录已经恢复。"),
    ]


def test_native_dialog_conversation_syncs_partial_turn_and_reused_raw_id(
    tmp_path,
) -> None:
    event_path = tmp_path / "dialog-events.jsonl"
    settings = Settings(
        hardware_driver="mock",
        motion_cooldown_seconds=0.0,
        data_path=tmp_path / "native-conversations.db",
        dialog_event_path=event_path,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        headers, _ = _login(client, code="native-realtime-user")
        pet_id = _bind(client, headers, serial="K1-REALTIME")["pet"]["pet_id"]
        created_at_ms = int(time.time() * 1000) + 100
        raw_conversation_id = "native-K1-REALTIME-1"
        user_event = {
            "event_id": "event-user-first",
            "conversation_id": raw_conversation_id,
            "device_serial": "K1-REALTIME",
            "role": "user",
            "content": "现在电量是多少？",
            "created_at_ms": created_at_ms,
        }
        event_path.write_text(
            json.dumps(user_event, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        partial = client.get(
            f"/api/v1/pets/{pet_id}/dialog-conversations",
            headers=headers,
        ).json()["data"]
        assert partial["sync"]["imported_messages"] == 1
        assert partial["conversations"][0]["status"] == "waiting_assistant"
        partial_growth = client.get(
            f"/api/v1/pets/{pet_id}/growth",
            headers=headers,
        ).json()["data"]
        assert partial_growth["recent_events"] == []
        first_conversation_id = partial["conversations"][0]["conversation_id"]
        first_revision = partial["sync"]["revision"]

        assistant_event = {
            "event_id": "event-assistant-first",
            "conversation_id": raw_conversation_id,
            "device_serial": "K1-REALTIME",
            "role": "assistant",
            "content": "当前电量为 78%。",
            "created_at_ms": created_at_ms + 1,
        }
        with event_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(assistant_event, ensure_ascii=False) + "\n")

        complete = client.get(
            f"/api/v1/pets/{pet_id}/dialog-conversations",
            headers=headers,
        ).json()["data"]
        assert complete["sync"]["imported_messages"] == 1
        assert complete["sync"]["revision"] != first_revision
        assert complete["conversations"][0]["conversation_id"] == first_conversation_id
        assert complete["conversations"][0]["status"] == "complete"
        complete_growth = client.get(
            f"/api/v1/pets/{pet_id}/growth",
            headers=headers,
        ).json()["data"]
        assert [
            event["event_type"] for event in complete_growth["recent_events"]
        ] == ["question"]
        detail = client.get(
            f"/api/v1/pets/{pet_id}/dialog-conversations/{first_conversation_id}",
            headers=headers,
        ).json()["data"]
        assert [item["content"] for item in detail["messages"]] == [
            "现在电量是多少？",
            "当前电量为 78%。",
        ]

        reused_events = [
            {
                "event_id": "event-user-second",
                "conversation_id": raw_conversation_id,
                "device_serial": "K1-REALTIME",
                "role": "user",
                "content": "请点点头。",
                "created_at_ms": created_at_ms + 2,
            },
            {
                "event_id": "event-assistant-second",
                "conversation_id": raw_conversation_id,
                "device_serial": "K1-REALTIME",
                "role": "assistant",
                "content": "好的。",
                "created_at_ms": created_at_ms + 3,
            },
        ]
        with event_path.open("a", encoding="utf-8") as stream:
            for event in reused_events:
                stream.write(json.dumps(event, ensure_ascii=False) + "\n")

        repeated = client.get(
            f"/api/v1/pets/{pet_id}/dialog-conversations",
            headers=headers,
        ).json()["data"]
        assert repeated["sync"]["conversation_count"] == 2
        assert len(repeated["conversations"]) == 2
        assert {item["preview"] for item in repeated["conversations"]} == {
            "现在电量是多少？",
            "请点点头。",
        }

        missing = client.get(
            f"/api/v1/pets/{pet_id}/dialog-conversations/missing-turn",
            headers=headers,
        )
        assert missing.status_code == 404


def test_critical_data_survives_application_restart(tmp_path) -> None:
    settings = Settings(
        hardware_driver="mock",
        motion_cooldown_seconds=0.0,
        data_path=tmp_path / "persistent.db",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        headers, _ = _login(client, code="persistent-user")
        bound = _bind(client, headers, serial="K1-PERSIST")
        pet_id = bound["pet"]["pet_id"]
        personality_id = bound["pet"]["personality_id"]
        client.post(
            f"/api/v1/pets/{pet_id}/interactions",
            headers=headers,
            json={"event_type": "completed_task", "request_id": "persist-event"},
        )
        client.post(
            f"/api/v1/pets/{pet_id}/dialogs",
            headers=headers,
            json={"content": "记住这次对话", "trigger_action": False},
        )

    restarted_app = create_app(settings)
    with TestClient(restarted_app) as restarted:
        headers, login = _login(restarted, code="persistent-user")
        assert login["pets"][0]["pet_id"] == pet_id
        dashboard = restarted.get(
            f"/api/v1/pets/{pet_id}/dashboard", headers=headers
        ).json()["data"]
        history = restarted.get(
            f"/api/v1/pets/{pet_id}/dialogs", headers=headers
        ).json()["data"]
        assert dashboard["pet"]["personality_id"] == personality_id
        assert dashboard["intimacy"]["points"] == 7
        assert len(history) == 2


def test_unbind_preserves_personality_but_isolates_dialog_history(
    client: TestClient,
) -> None:
    first_headers, _ = _login(client, code="first-owner")
    first_bound = _bind(client, first_headers, serial="K1-TRANSFER")
    pet_id = first_bound["pet"]["pet_id"]
    personality_id = first_bound["pet"]["personality_id"]
    client.post(
        f"/api/v1/pets/{pet_id}/dialogs",
        headers=first_headers,
        json={"content": "第一位用户的隐私", "trigger_action": False},
    )
    client.post(f"/api/v1/pets/{pet_id}/unbind", headers=first_headers)

    second_headers, _ = _login(client, code="second-owner")
    second_bound = _bind(client, second_headers, serial="K1-TRANSFER")
    second_history = client.get(
        f"/api/v1/pets/{pet_id}/dialogs", headers=second_headers
    ).json()["data"]
    assert second_bound["blind_box_revealed"] is False
    assert second_bound["pet"]["personality_id"] == personality_id
    assert second_history == []
