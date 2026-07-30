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


def test_repeated_wake_is_idempotent(client: TestClient) -> None:
    first = client.post("/api/v1/dialog/wake")
    second = client.post("/api/v1/dialog/wake")

    assert first.json()["data"]["changed"] is True
    assert second.json()["data"]["changed"] is False
