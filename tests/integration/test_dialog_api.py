from fastapi.testclient import TestClient


def test_wake_and_interrupt_dialog(client: TestClient) -> None:
    wake = client.post("/api/v1/dialog/wake")
    status_after_wake = client.get("/api/v1/device/status").json()["data"]
    interrupt = client.post("/api/v1/dialog/interrupt")
    status_after_interrupt = client.get("/api/v1/device/status").json()["data"]

    assert wake.status_code == 200
    assert status_after_wake["dialog_state"] == "awake"
    assert interrupt.status_code == 200
    assert status_after_interrupt["dialog_state"] == "interrupted"


def test_repeated_wake_requests_another_listening_turn(client: TestClient) -> None:
    first = client.post("/api/v1/dialog/wake")
    second = client.post("/api/v1/dialog/wake")

    assert first.json()["data"]["changed"] is True
    assert second.json()["data"]["changed"] is True


def test_dialog_status_is_visible(client: TestClient) -> None:
    response = client.get("/api/v1/dialog/status")

    assert response.status_code == 200
    assert response.json()["data"]["state"] == "ready"
    assert response.json()["data"]["source"] == "mock"


def test_proactive_speech_is_accepted_only_while_idle(client: TestClient) -> None:
    first = client.post(
        "/api/v1/dialog/speak",
        json={"content": "我在这里呀。", "request_id": "auto-speech-1"},
    )
    second = client.post(
        "/api/v1/dialog/speak",
        json={"content": "今天也要开心哦。", "request_id": "auto-speech-2"},
    )

    assert first.status_code == 200
    assert first.json()["data"]["dialog_state"] == "queued"
    assert second.status_code == 409


def test_text_dialog_is_accepted_once_and_rejects_busy_session(
    client: TestClient,
) -> None:
    first = client.post(
        "/api/v1/dialog/text",
        json={"content": "现在电量是多少？", "request_id": "text-test-1"},
    )
    second = client.post(
        "/api/v1/dialog/text",
        json={"content": "请点点头", "request_id": "text-test-2"},
    )

    assert first.status_code == 200
    assert first.json()["data"] == {
        "dialog_state": "queued",
        "request_id": "text-test-1",
        "changed": True,
    }
    assert second.status_code == 409
    assert second.json()["data"]["details"]["dialog_state"] == "thinking"


def test_text_dialog_rejects_blank_control_and_oversized_content(
    client: TestClient,
) -> None:
    blank = client.post("/api/v1/dialog/text", json={"content": "   "})
    control = client.post("/api/v1/dialog/text", json={"content": "问题\n继续"})
    oversized = client.post("/api/v1/dialog/text", json={"content": "问" * 501})

    assert blank.status_code == 422
    assert control.status_code == 422
    assert oversized.status_code == 422


def test_dialog_follow_up_config_is_persisted(client: TestClient) -> None:
    initial = client.get("/api/v1/dialog/config")
    updated = client.patch(
        "/api/v1/dialog/config",
        json={"follow_up_seconds": 45},
    )
    current = client.get("/api/v1/dialog/config")

    assert initial.status_code == 200
    assert initial.json()["data"]["follow_up_seconds"] == 30
    assert initial.json()["data"]["source"] == "default"
    assert updated.status_code == 200
    assert updated.json()["data"]["follow_up_seconds"] == 45
    assert updated.json()["data"]["source"] == "persisted"
    assert current.json()["data"]["follow_up_seconds"] == 45


def test_dialog_follow_up_config_rejects_unsafe_range(client: TestClient) -> None:
    too_short = client.patch(
        "/api/v1/dialog/config",
        json={"follow_up_seconds": 4},
    )
    too_long = client.patch(
        "/api/v1/dialog/config",
        json={"follow_up_seconds": 121},
    )

    assert too_short.status_code == 422
    assert too_long.status_code == 422
