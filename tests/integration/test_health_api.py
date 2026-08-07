from fastapi.testclient import TestClient


def test_health_returns_mock_status(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "ok"
    assert response.json()["data"]["adapter"] == "mock"
