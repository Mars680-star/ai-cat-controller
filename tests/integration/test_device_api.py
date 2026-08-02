from fastapi.testclient import TestClient


def test_device_status_is_connected(client: TestClient) -> None:
    response = client.get("/api/v1/device/status")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["connected"] is True
    assert data["adapter_mode"] == "mock"
    assert data["current_action"] == "idle"
    assert data["battery_available"] is True
    assert data["battery_percent"] == 86
    assert data["battery_status"] == "discharging"
    assert data["battery_voltage_mv"] == 3900


def test_device_status_does_not_expose_secrets(client: TestClient) -> None:
    serialized = client.get("/api/v1/device/status").text.lower()

    assert "api_key" not in serialized
    assert "product_secret" not in serialized
    assert "environment" not in serialized


def test_services_status_has_confirmed_services(client: TestClient) -> None:
    response = client.get("/api/v1/services/status")

    assert response.status_code == 200
    services = response.json()["data"]["services"]
    assert {item["service_name"] for item in services} == {
        "volc-pulseaudio.service",
        "volc-conv-ai.service",
        "volc-k1-wake-word.service",
    }
    assert all(item["active"] for item in services)
