import time
import json

from fastapi.testclient import TestClient

from ai_cat_controller.core.config import Settings
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
    assert intimacy["points"] == 20
    assert intimacy["level"]["level"] == 1
    assert intimacy["daily_growth_cap"] == 20
    assert any(event["points_delta"] == 0 for event in intimacy["history"])


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
    assert dialog_data["voice_id"].startswith("mock_voice_")
    assert dialog_data["voice_integration_status"] == "mock_only"

    history = client.get(
        f"/api/v1/pets/{pet_id}/dialogs", headers=headers
    ).json()["data"]
    assert [message["role"] for message in history] == ["assistant", "user"]

    settings = client.patch(
        f"/api/v1/pets/{pet_id}/settings",
        headers=headers,
        json={"name": "安心", "volume": 35},
    ).json()["data"]
    assert settings["name"] == "安心"
    assert settings["volume"] == 35

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
