from fastapi.testclient import TestClient

from ai_cat_controller.core.config import Settings
from ai_cat_controller.main import create_app


def test_local_k1_motion_returns_501_without_running_command() -> None:
    app = create_app(
        Settings(
            hardware_driver="local_k1",
            api_key_enabled=True,
            api_key="test-only-key",
        )
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/motion/head/shake",
            headers={"X-API-Key": "test-only-key"},
            json={"intensity": 0.5, "duration_ms": 600},
        )

    assert response.status_code == 501
    assert response.json()["data"]["code"] == "adapter_not_implemented"
