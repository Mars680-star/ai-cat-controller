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
