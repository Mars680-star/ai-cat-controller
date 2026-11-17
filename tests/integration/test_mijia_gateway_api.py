import json

from fastapi.testclient import TestClient


def _login_and_bind(
    client: TestClient,
    *,
    code: str = "mijia-owner",
    serial: str = "K1-MIJIA-0001",
) -> tuple[dict[str, str], str]:
    login = client.post(
        "/api/v1/auth/mock-login",
        json={"login_code": code, "nickname": "米家用户"},
    )
    assert login.status_code == 200
    headers = {"X-Mock-Session": login.json()["data"]["session_token"]}
    bound = client.post(
        "/api/v1/pets/bind",
        headers=headers,
        json={
            "device_serial": serial,
            "pet_name": "小安",
            "network_name": "Home Wi-Fi",
        },
    )
    assert bound.status_code == 200
    return headers, bound.json()["data"]["pet"]["pet_id"]


def _prepare(
    client: TestClient,
    headers: dict[str, str],
    pet_id: str,
    *,
    request_id: str = "mijia-prepare-1",
) -> dict:
    response = client.post(
        f"/api/v1/integrations/mijia/pets/{pet_id}/prepare",
        headers=headers,
        json={"request_id": request_id},
    )
    assert response.status_code == 200
    return response.json()["data"]


def test_mijia_status_and_spec_do_not_claim_a_platform_link(
    client: TestClient,
) -> None:
    headers, pet_id = _login_and_bind(client)

    status = client.get(
        f"/api/v1/integrations/mijia/pets/{pet_id}/status",
        headers=headers,
    )
    spec = client.get(
        f"/api/v1/integrations/mijia/pets/{pet_id}/spec",
        headers=headers,
    )

    assert status.status_code == 200
    assert status.json()["data"] == {
        "provider": "mijia",
        "status": "not_prepared",
        "gateway_device_id": None,
        "platform_linked": False,
        "credentials_configured": False,
        "miot_model": None,
        "spec_revision": "mijia-preparation-v1",
        "prepared_at": None,
        "updated_at": None,
        "changed": False,
        "duplicate": False,
    }
    assert spec.status_code == 200
    contract = spec.json()["data"]
    assert contract["mapping_state"] == "awaiting_platform_spec"
    assert contract["miot_model"] is None
    assert all(item["siid"] is None for item in contract["services"])
    assert all(
        item["siid"] is None and item["piid"] is None
        for item in contract["properties"]
    )
    assert all(
        item["siid"] is None and item["aiid"] is None
        for item in contract["actions"]
    )
    assert all(
        item["siid"] is None and item["eiid"] is None
        for item in contract["events"]
    )
    serialized = json.dumps(
        {"status": status.json()["data"], "spec": contract}
    ).lower()
    for secret_field in ("access_token", "refresh_token", "client_secret", "callback_url"):
        assert secret_field not in serialized


def test_mijia_prepare_is_idempotent_and_owner_scoped(client: TestClient) -> None:
    owner_headers, pet_id = _login_and_bind(client)

    first = _prepare(client, owner_headers, pet_id)
    duplicate = _prepare(client, owner_headers, pet_id)

    assert first["status"] == "prepared"
    assert first["gateway_device_id"].startswith("mijia_")
    assert first["changed"] is True
    assert first["duplicate"] is False
    assert duplicate["gateway_device_id"] == first["gateway_device_id"]
    assert duplicate["changed"] is False
    assert duplicate["duplicate"] is True
    assert duplicate["platform_linked"] is False

    other_headers, _ = _login_and_bind(
        client,
        code="mijia-other-owner",
        serial="K1-MIJIA-OTHER",
    )
    hidden = client.get(
        f"/api/v1/integrations/mijia/pets/{pet_id}/status",
        headers=other_headers,
    )
    assert hidden.status_code == 404


def test_mijia_gateway_snapshot_and_property_whitelist(client: TestClient) -> None:
    headers, pet_id = _login_and_bind(client)
    gateway_id = _prepare(client, headers, pet_id)["gateway_device_id"]

    snapshot = client.get(
        f"/api/v1/gateway/mijia/v1/devices/{gateway_id}/snapshot"
    )
    read = client.post(
        f"/api/v1/gateway/mijia/v1/devices/{gateway_id}/properties/read",
        json={
            "properties": [
                "online",
                "battery_level",
                "charging",
                "volume",
                "intimacy_level",
                "intimacy_points",
                "dialog_state",
                "motion_state",
            ]
        },
    )

    assert snapshot.status_code == 200
    snapshot_data = snapshot.json()["data"]
    assert set(snapshot_data) == {
        "gateway_device_id",
        "spec_revision",
        "device",
        "intimacy",
        "states",
        "available_action_ids",
        "updated_at",
    }
    assert set(snapshot_data["device"]) == {
        "name",
        "online",
        "battery_level",
        "charging",
        "volume",
    }
    serialized = json.dumps(snapshot_data).lower()
    for engineering_field in (
        "hostname",
        "platform",
        "python_version",
        "service_name",
        "adapter_mode",
        "serial_number",
    ):
        assert engineering_field not in serialized

    assert read.status_code == 200
    values = read.json()["data"]["properties"]
    assert set(values) == {
        "online",
        "battery_level",
        "charging",
        "volume",
        "intimacy_level",
        "intimacy_points",
        "dialog_state",
        "motion_state",
    }
    assert values["volume"] == 60
    assert values["motion_state"] == "idle"

    rejected = client.post(
        f"/api/v1/gateway/mijia/v1/devices/{gateway_id}/properties/read",
        json={"properties": ["hostname"]},
    )
    assert rejected.status_code == 422


def test_mijia_gateway_write_and_action_are_fixed_and_idempotent(
    client: TestClient,
) -> None:
    headers, pet_id = _login_and_bind(client)
    gateway_id = _prepare(client, headers, pet_id)["gateway_device_id"]
    write_url = f"/api/v1/gateway/mijia/v1/devices/{gateway_id}/properties/write"

    first = client.post(
        write_url,
        json={
            "property_id": "volume",
            "value": 35,
            "request_id": "mijia-volume-1",
        },
    )
    duplicate = client.post(
        write_url,
        json={
            "property_id": "volume",
            "value": 35,
            "request_id": "mijia-volume-1",
        },
    )

    assert first.status_code == 200
    assert first.json()["data"] == {
        "property_id": "volume",
        "value": 35,
        "request_id": "mijia-volume-1",
        "duplicate": False,
    }
    assert duplicate.status_code == 200
    assert duplicate.json()["data"]["duplicate"] is True

    conflicting_reuse = client.post(
        write_url,
        json={
            "property_id": "volume",
            "value": 40,
            "request_id": "mijia-volume-1",
        },
    )
    assert conflicting_reuse.status_code == 409

    raw_parameter = client.post(
        write_url,
        json={
            "property_id": "volume",
            "value": 35,
            "request_id": "mijia-volume-raw",
            "sink": "arbitrary-output",
        },
    )
    assert raw_parameter.status_code == 422

    action_url = (
        f"/api/v1/gateway/mijia/v1/devices/{gateway_id}/actions/head_nod"
    )
    action = client.post(action_url, json={"request_id": "mijia-action-1"})
    action_duplicate = client.post(
        action_url,
        json={"request_id": "mijia-action-1"},
    )
    assert action.status_code == 202
    assert action.json()["data"]["action_id"] == "head_nod"
    assert action.json()["data"]["duplicate"] is False
    assert action_duplicate.status_code == 202
    assert action_duplicate.json()["data"]["duplicate"] is True

    raw_motion = client.post(
        action_url,
        json={"request_id": "mijia-action-raw", "angle": 90, "speed": 1},
    )
    unknown_action = client.post(
        f"/api/v1/gateway/mijia/v1/devices/{gateway_id}/actions/raw_command",
        json={"request_id": "mijia-action-unknown"},
    )
    assert raw_motion.status_code == 422
    assert unknown_action.status_code == 422


def test_mijia_remove_and_unbind_revoke_the_gateway_id(client: TestClient) -> None:
    headers, pet_id = _login_and_bind(client)
    first_gateway_id = _prepare(client, headers, pet_id)["gateway_device_id"]

    removed = client.post(
        f"/api/v1/integrations/mijia/pets/{pet_id}/remove",
        headers=headers,
        json={"request_id": "mijia-remove-1"},
    )
    assert removed.status_code == 200
    assert removed.json()["data"]["status"] == "not_prepared"
    assert removed.json()["data"]["gateway_device_id"] is None
    assert client.get(
        f"/api/v1/gateway/mijia/v1/devices/{first_gateway_id}/snapshot"
    ).status_code == 404

    second_gateway_id = _prepare(
        client,
        headers,
        pet_id,
        request_id="mijia-prepare-2",
    )["gateway_device_id"]
    assert second_gateway_id != first_gateway_id

    unbound = client.post(
        f"/api/v1/pets/{pet_id}/unbind",
        headers=headers,
        json={},
    )
    assert unbound.status_code == 200
    assert client.get(
        f"/api/v1/gateway/mijia/v1/devices/{second_gateway_id}/snapshot"
    ).status_code == 404
