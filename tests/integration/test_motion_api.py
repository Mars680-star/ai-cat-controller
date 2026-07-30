import time

import pytest
from fastapi.testclient import TestClient


def wait_for_idle(client: TestClient, timeout: float = 2.0) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = client.get("/api/v1/device/status").json()["data"]
        if data["current_action"] == "idle":
            return data
        time.sleep(0.01)
    raise AssertionError("motion did not return to idle")


@pytest.mark.parametrize(
    ("endpoint", "action"),
    [
        ("/api/v1/motion/head/shake", "head_shake"),
        ("/api/v1/motion/head/nod", "head_nod"),
        ("/api/v1/motion/tail/wag", "tail_wag"),
    ],
)
def test_motion_is_accepted_and_completes(
    client: TestClient, endpoint: str, action: str
) -> None:
    response = client.post(
        endpoint,
        json={"intensity": 0.5, "duration_ms": 100},
    )

    assert response.status_code == 202
    assert response.json()["data"]["action"] == action
    assert wait_for_idle(client)["last_action"] == action


@pytest.mark.parametrize(
    "payload",
    [
        {"intensity": 0.0, "duration_ms": 600},
        {"intensity": 1.1, "duration_ms": 600},
        {"intensity": 0.5, "duration_ms": 99},
        {"intensity": 0.5, "duration_ms": 3001},
        {"intensity": 0.5, "duration_ms": 600, "request_id": "   "},
        {"intensity": 0.5, "duration_ms": 600, "request_id": "bad id"},
    ],
)
def test_invalid_motion_request_returns_uniform_422(
    client: TestClient, payload: dict[str, object]
) -> None:
    response = client.post("/api/v1/motion/head/shake", json=payload)

    assert response.status_code == 422
    body = response.json()
    assert body["success"] is False
    assert body["data"]["code"] == "validation_error"


def test_second_motion_conflicts(client: TestClient) -> None:
    first = client.post(
        "/api/v1/motion/head/shake",
        json={"intensity": 0.5, "duration_ms": 500},
    )
    second = client.post(
        "/api/v1/motion/tail/wag",
        json={"intensity": 0.5, "duration_ms": 100},
    )

    assert first.status_code == 202
    assert second.status_code == 409
    assert second.json()["data"]["code"] == "action_conflict"
    client.post("/api/v1/motion/stop")


def test_stop_cancels_current_motion(client: TestClient) -> None:
    client.post(
        "/api/v1/motion/head/shake",
        json={"intensity": 0.5, "duration_ms": 1000},
    )

    response = client.post("/api/v1/motion/stop")

    assert response.status_code == 200
    assert response.json()["data"]["stopped"] is True
    assert client.get("/api/v1/device/status").json()["data"]["current_action"] == "idle"


def test_stop_is_idempotent(client: TestClient) -> None:
    response = client.post("/api/v1/motion/stop")

    assert response.status_code == 200
    assert response.json()["data"] == {"stopped": False, "state": "idle"}


def test_cancelled_old_task_does_not_clear_new_motion(client: TestClient) -> None:
    client.post(
        "/api/v1/motion/head/shake",
        json={"intensity": 0.5, "duration_ms": 1000},
    )
    client.post("/api/v1/motion/stop")
    second = client.post(
        "/api/v1/motion/tail/wag",
        json={"intensity": 0.5, "duration_ms": 300},
    )

    assert second.status_code == 202
    status = client.get("/api/v1/device/status").json()["data"]
    assert status["current_action"] == "tail_wag"
    assert wait_for_idle(client)["last_action"] == "tail_wag"
