from fastapi.testclient import TestClient


def test_root_redirects_to_control(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/control"


def test_control_page_is_self_contained(client: TestClient) -> None:
    response = client.get("/control")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "AI 猫伴侣" in response.text
    assert "停止当前动作" in response.text
    assert "性格盲盒" in response.text
    assert "安全预设动作" in response.text
    assert "真机语音状态" in response.text
    assert "开始或继续聆听" in response.text
    assert 'id="real-device-status"' in response.text
    assert 'id="real-battery-percent"' in response.text
    assert "https://" not in response.text
    assert "cdn" not in response.text.lower()


def test_static_assets_are_available(client: TestClient) -> None:
    assert client.get("/static/control.css").status_code == 200
    script = client.get("/static/control.js")
    assert script.status_code == 200
    assert 'apiRequest("/api/v1/device/status")' in script.text
    assert "batteryStateLabels" in script.text
    assert 'realDeviceStatus.classList.toggle("hidden", !isLocalK1)' in script.text
    assert 'deviceStatusForm.classList.toggle("hidden", isLocalK1)' in script.text
    assert client.get("/static/ai-cat-avatar.png").status_code == 200


def test_control_page_supports_head_check(client: TestClient) -> None:
    response = client.head("/control")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
