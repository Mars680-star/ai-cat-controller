from fastapi.testclient import TestClient

from ai_cat_controller.core.config import Settings
from ai_cat_controller.main import create_app


def test_api_key_is_disabled_by_default(client: TestClient) -> None:
    assert client.get("/api/v1/device/status").status_code == 200


def test_api_key_protects_versioned_api() -> None:
    app = create_app(
        Settings(
            hardware_driver="mock",
            api_key_enabled=True,
            api_key="correct-secret",
            motion_cooldown_seconds=0.0,
        )
    )

    with TestClient(app) as client:
        missing = client.get("/api/v1/device/status")
        wrong = client.get(
            "/api/v1/device/status", headers={"X-API-Key": "wrong-secret"}
        )
        correct = client.get(
            "/api/v1/device/status", headers={"X-API-Key": "correct-secret"}
        )
        health = client.get("/health")
        control = client.get("/control")

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert correct.status_code == 200
    assert health.status_code == 200
    assert control.status_code == 200
    assert "correct-secret" not in correct.text
