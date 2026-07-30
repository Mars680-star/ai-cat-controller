from fastapi.testclient import TestClient


def test_root_redirects_to_control(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/control"


def test_control_page_is_self_contained(client: TestClient) -> None:
    response = client.get("/control")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "AI 猫控制器" in response.text
    assert "立即停止动作" in response.text
    assert "https://" not in response.text
    assert "cdn" not in response.text.lower()


def test_static_assets_are_available(client: TestClient) -> None:
    assert client.get("/static/control.css").status_code == 200
    assert client.get("/static/control.js").status_code == 200


def test_control_page_supports_head_check(client: TestClient) -> None:
    response = client.head("/control")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
