"""Computer-only gateway security checks; no real sockets or hardware are used."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import get_args

import httpx
import pytest
from fastapi.testclient import TestClient

from ai_cat_controller.mini_gateway.dialog_models import State
from ai_cat_controller.mini_gateway.status_reader import _DIALOG_STATES


API_KEY = "device-test-api-key-not-real"
CLIENT_TOKEN = "gateway-client-test-token-0123456789"
SERIAL = "7c2b63fd4a128"
UPSTREAM = "http://192.168.1.152:8000"
PRIVATE_TEXT = "PRIVATE-UPSTREAM-TEXT-/root/secret.env"
FIXED_PATHS = {"/health", "/api/v1/device/status", "/api/v1/dialog/status"}
DEVICE_HEADER = "X-AI-Cat-Device-SHA256"


def test_status_bridge_and_reader_allowlists_match_the_public_native_contract(gateway):
    assert gateway.DIALOG_STATES == _DIALOG_STATES == set(get_args(State))


@pytest.mark.parametrize("phase", ["processing_audio", "followup_preparing", "connected",
    "proactive_queued", "proactive_speaking", "proactive_finishing", "stopping"])
def test_native_transition_phases_are_not_relabelled_unavailable(gateway, payloads, phase):
    config = gateway.GatewayConfig(upstream_url=UPSTREAM, api_key=API_KEY, client_token=CLIENT_TOKEN)
    payloads["/api/v1/dialog/status"]["data"]["state"] = phase
    value = gateway._normalize(config, payloads["/health"]["data"],
        payloads["/api/v1/device/status"]["data"], payloads["/api/v1/dialog/status"]["data"])
    assert value["dialog"]["state"] == phase and value["dialog"]["elapsedSeconds"] is not None
    assert value["readOnly"] is True
    assert PRIVATE_TEXT not in str(value)


@pytest.fixture(scope="module")
def gateway():
    source = Path(__file__).resolve().parents[2] / "tools/miniprogram_dev_gateway.py"
    spec = importlib.util.spec_from_file_location("tested_miniprogram_dev_gateway", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def payloads():
    """Use actual backend field names, with private extras to catch raw forwarding."""
    return {
        "/health": {
            "success": True,
            "message": PRIVATE_TEXT,
            "data": {"status": "ok", "version": "0.1.0", "adapter": "local_k1"},
        },
        "/api/v1/device/status": {
            "success": True,
            "message": PRIVATE_TEXT,
            "data": {
                "adapter_mode": "local_k1",
                "device_serial": SERIAL,
                "connected": True,
                "hostname": PRIVATE_TEXT,
                "platform": PRIVATE_TEXT,
                "python_version": PRIVATE_TEXT,
                "current_action": "idle",
                "last_action": PRIVATE_TEXT,
                "dialog_state": "thinking",
                "head_state": "idle",
                "tail_state": "disabled",
                "action_count": 7,
                "last_action_at": None,
                "last_error": PRIVATE_TEXT,
                "adapter_uptime_seconds": 100.0,
                "uptime_seconds": 90.0,
                "battery_available": True,
                "battery_percent": 83,
                "battery_status": "charging",
                "battery_present": True,
                "battery_voltage_mv": 4012,
                "charging": True,
                "charger_online": True,
                "battery_error": PRIVATE_TEXT,
                "output_volume_available": True,
                "output_volume_percent": 45,
                "output_muted": False,
                "output_volume_error": PRIVATE_TEXT,
                "api_key": API_KEY,
            },
        },
        "/api/v1/dialog/status": {
            "success": True,
            "message": PRIVATE_TEXT,
            "data": {
                "state": "thinking",
                "message": PRIVATE_TEXT,
                "session_active": True,
                "can_interrupt": True,
                "audio_playing": False,
                "follow_up_deadline_ms": 0,
                "updated_at_ms": 1,
                "sequence": 5,
                "source": PRIVATE_TEXT,
                "stale": False,
                "personality_id": PRIVATE_TEXT,
                "personality_revision": PRIVATE_TEXT,
                "voice_type": PRIVATE_TEXT,
                "api_key": API_KEY,
            },
        },
    }


def make_config(gateway, **changes):
    values = {"upstream_url": UPSTREAM, "api_key": API_KEY, "client_token": CLIENT_TOKEN}
    values.update(changes)
    return gateway.GatewayConfig(**values)


def make_client(gateway, payloads, *, handler=None, **config_changes):
    requests = []

    async def dispatch(request):
        requests.append(request)
        if handler is not None:
            result = handler(request)
            if hasattr(result, "__await__"):
                result = await result
            return result
        return httpx.Response(200, json=payloads[request.url.path])

    app = gateway.create_app(
        make_config(gateway, **config_changes), transport=httpx.MockTransport(dispatch)
    )
    return TestClient(app), requests


def status(client, **kwargs):
    return client.get("/v1/status", headers={"Authorization": f"Bearer {CLIENT_TOKEN}"}, **kwargs)


def assert_safe_failure(response):
    assert response.status_code == 502
    body = response.text
    for private in (API_KEY, CLIENT_TOKEN, SERIAL, PRIVATE_TEXT, UPSTREAM):
        assert private not in body
    assert response.json().get("success") is False
    assert DEVICE_HEADER not in response.headers


def test_gateway_health_is_public_and_never_contacts_device(gateway, payloads):
    client, requests = make_client(gateway, payloads)
    with client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"success": True, "data": {"status": "ok", "readOnly": True}}
    assert not requests
    assert API_KEY not in response.text and CLIENT_TOKEN not in response.text


@pytest.mark.parametrize("authorization", [None, "", "Basic bad", "Bearer wrong", f"Bearer {API_KEY}"])
def test_status_requires_separate_temporary_token(gateway, payloads, authorization):
    client, requests = make_client(gateway, payloads)
    with client:
        response = client.get("/v1/status", headers={} if authorization is None else {"Authorization": authorization})
    assert response.status_code == 401
    assert not requests
    assert API_KEY not in response.text and CLIENT_TOKEN not in response.text


def test_status_only_calls_fixed_paths_and_projects_allowlisted_fields(gateway, payloads):
    client, requests = make_client(gateway, payloads, expected_serial=SERIAL)
    with client:
        response = client.get("/v1/status", headers={
            "Authorization": f"Bearer {CLIENT_TOKEN}",
            "X-API-Key": "attacker-supplied-key",
            "Cookie": "private-session=must-not-forward",
            "X-Forwarded-Host": "evil.example",
        })
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert len(requests) == 3
    assert {request.url.path for request in requests} == FIXED_PATHS
    for request in requests:
        assert request.method == "GET"
        assert str(request.url).startswith(UPSTREAM + "/")
        assert not request.url.query and not request.content
        assert "authorization" not in request.headers
        assert "cookie" not in request.headers and "x-forwarded-host" not in request.headers
        expected_key = None if request.url.path == "/health" else API_KEY
        assert request.headers.get("x-api-key") == expected_key

    envelope = response.json()
    assert envelope["success"] is True
    data = envelope["data"]
    assert set(data) == {"readOnly", "sampledAt", "health", "device", "dialog"}
    assert data["readOnly"] is True
    assert data["sampledAt"].endswith("Z"), "The shipped mini-program client expects canonical UTC Z"
    sampled = datetime.fromisoformat(data["sampledAt"].replace("Z", "+00:00"))
    assert sampled.utcoffset() == timezone.utc.utcoffset(sampled)
    assert data["health"] == {"status": "ok", "version": "0.1.0", "adapter": "local_k1"}
    device = data["device"]
    assert set(device) == {"connected", "deviceSerial", "networkName", "batteryPercent", "batteryState", "batteryVoltage", "chargerOnline", "batteryPresent", "lastSeenAt"}
    assert device["connected"] is True
    assert device["deviceSerial"] is None or SERIAL not in device["deviceSerial"]
    assert device["networkName"] is None and device["lastSeenAt"] is None
    assert device["batteryPercent"] == 83
    assert device["batteryState"] == "charging"
    assert device["batteryVoltage"] == pytest.approx(4.012)
    assert device["chargerOnline"] is True and device["batteryPresent"] is True
    dialog = data["dialog"]
    assert set(dialog) == {"state", "elapsedSeconds", "followUpRemaining", "canInterrupt", "sessionActive", "stale"}
    assert dialog["state"] == "thinking"
    assert dialog["sessionActive"] is True and dialog["canInterrupt"] is True
    assert dialog["stale"] is False
    assert isinstance(dialog["elapsedSeconds"], int) and dialog["elapsedSeconds"] >= 0
    assert isinstance(dialog["followUpRemaining"], int) and 0 <= dialog["followUpRemaining"] <= 120
    for private in (API_KEY, CLIENT_TOKEN, SERIAL, PRIVATE_TEXT, "attacker-supplied-key", "must-not-forward"):
        assert private not in response.text


def test_success_publishes_full_serial_hash_only_in_header_and_preserves_json(gateway, payloads, monkeypatch):
    fixed = datetime(2026, 10, 20, 4, 5, 6, tzinfo=timezone.utc)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    monkeypatch.setattr(gateway, "datetime", FixedDateTime)
    client, _ = make_client(gateway, payloads, expected_serial=SERIAL)
    old_client, _ = make_client(gateway, payloads)
    with client, old_client:
        response = client.get("/v1/status", headers={"Authorization": f"Bearer {CLIENT_TOKEN}", DEVICE_HEADER: "forged-client-value"})
        legacy_response = status(old_client)
    digest = hashlib.sha256(SERIAL.encode("ascii")).hexdigest()
    assert response.status_code == legacy_response.status_code == 200
    assert response.headers[DEVICE_HEADER] == digest
    assert legacy_response.headers[DEVICE_HEADER] == digest
    assert response.json() == legacy_response.json()
    assert digest not in response.text and SERIAL not in response.text
    assert response.json()["data"]["device"]["deviceSerial"] == SERIAL[:3] + "****" + SERIAL[-4:]


def test_full_serial_mismatch_with_identical_mask_has_no_identity_header(gateway, payloads):
    other = "7c2another-devicea128"
    assert SERIAL[:3] + "****" + SERIAL[-4:] == other[:3] + "****" + other[-4:]
    payloads["/api/v1/device/status"]["data"]["device_serial"] = other
    client, _ = make_client(gateway, payloads, expected_serial=SERIAL)
    with client:
        response = status(client)
    assert_safe_failure(response)
    assert other not in response.text


@pytest.mark.parametrize("method,path,headers,expected", [
    ("GET", "/v1/status", {}, 401),
    ("GET", "/v1/status?extra=1", {"Authorization": f"Bearer {CLIENT_TOKEN}"}, 400),
    ("POST", "/v1/status", {"Authorization": f"Bearer {CLIENT_TOKEN}"}, 405),
    ("GET", "/missing", {"Authorization": f"Bearer {CLIENT_TOKEN}"}, 404),
    ("GET", "/health", {}, 200),
])
def test_errors_and_health_never_publish_device_identity(gateway, payloads, method, path, headers, expected):
    client, requests = make_client(gateway, payloads, expected_serial=SERIAL)
    with client:
        response = client.request(method, path, headers=headers)
    assert response.status_code == expected
    assert DEVICE_HEADER not in response.headers
    assert not requests


def test_unknown_serial_remains_null_without_an_invented_identity_header(gateway, payloads):
    payloads["/api/v1/device/status"]["data"]["device_serial"] = None
    client, _ = make_client(gateway, payloads)
    with client:
        response = status(client)
    assert response.status_code == 200
    assert response.json()["data"]["device"]["deviceSerial"] is None
    assert DEVICE_HEADER not in response.headers


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_mutating_methods_never_reach_device(gateway, payloads, method):
    client, requests = make_client(gateway, payloads)
    with client:
        response = client.request(method, "/v1/status", headers={"Authorization": f"Bearer {CLIENT_TOKEN}"}, json={"action": "nod_head"})
    assert response.status_code == 405
    assert not requests


@pytest.mark.parametrize("path", ["/api/v1/device/status", "/v1/proxy", "/v1/status/../motion/stop", "/v1/status/anything"])
def test_arbitrary_paths_are_not_proxy_routes(gateway, payloads, path):
    client, requests = make_client(gateway, payloads)
    with client:
        response = client.get(path, headers={"Authorization": f"Bearer {CLIENT_TOKEN}"})
    assert response.status_code == 404
    assert not requests


@pytest.mark.parametrize("kind", ["query", "body"])
def test_status_rejects_query_and_body_before_upstream(gateway, payloads, kind):
    client, requests = make_client(gateway, payloads)
    with client:
        response = client.request(
            "GET", "/v1/status?path=/api/v1/motion/stop" if kind == "query" else "/v1/status",
            headers={"Authorization": f"Bearer {CLIENT_TOKEN}"},
            content=b'{"command":"stop"}' if kind == "body" else b"",
        )
    assert response.status_code == 400
    assert not requests


def test_body_cannot_hide_behind_forged_zero_content_length(gateway, payloads):
    client, requests = make_client(gateway, payloads)
    with client:
        response = client.request(
            "GET", "/v1/status", content=b"must-not-be-accepted",
            headers={"Authorization": f"Bearer {CLIENT_TOKEN}", "Content-Length": "0"},
        )
    assert response.status_code == 400
    assert not requests


@pytest.mark.parametrize("failure", ["http401", "http500", "redirect", "timeout", "bad_json", "too_large"])
def test_upstream_failures_are_sanitized_without_mock_fallback(gateway, payloads, failure):
    def dispatch(request):
        if request.url.path != "/api/v1/dialog/status":
            return httpx.Response(200, json=payloads[request.url.path])
        if failure == "timeout":
            raise httpx.ReadTimeout(f"{PRIVATE_TEXT} {API_KEY}", request=request)
        if failure == "bad_json":
            return httpx.Response(200, text=f"not-json {PRIVATE_TEXT} {API_KEY}")
        if failure == "too_large":
            return httpx.Response(200, json={"success": True, "data": {"message": PRIVATE_TEXT * 3000}})
        if failure == "redirect":
            return httpx.Response(302, headers={"Location": f"http://127.0.0.1:1/{API_KEY}"}, text=PRIVATE_TEXT)
        return httpx.Response(401 if failure == "http401" else 500, text=f"{PRIVATE_TEXT} {API_KEY}")

    client, requests = make_client(gateway, payloads, handler=dispatch)
    with client:
        response = status(client)
    assert_safe_failure(response)
    assert len(requests) <= 3
    assert {request.url.path for request in requests} <= FIXED_PATHS
    assert "device" not in response.json().get("data", {})


@pytest.mark.parametrize("change", ["health_mock", "device_mock", "failure_flag", "missing_data", "wrong_serial", "not_object"])
def test_mock_mixed_or_incomplete_live_payload_is_rejected(gateway, payloads, change):
    if change == "health_mock":
        payloads["/health"]["data"]["adapter"] = "mock"
    elif change == "device_mock":
        payloads["/api/v1/device/status"]["data"]["adapter_mode"] = "mock"
    elif change == "failure_flag":
        payloads["/api/v1/dialog/status"]["success"] = False
    elif change == "missing_data":
        del payloads["/api/v1/device/status"]["data"]
    elif change == "wrong_serial":
        payloads["/api/v1/device/status"]["data"]["device_serial"] = "wrong-device-identity"
    elif change == "not_object":
        payloads["/api/v1/dialog/status"] = []
    client, _ = make_client(gateway, payloads, expected_serial=SERIAL)
    with client:
        response = status(client)
    assert_safe_failure(response)


def test_missing_battery_stays_unknown_not_zero(gateway, payloads):
    payloads["/api/v1/device/status"]["data"]["battery_available"] = False
    client, _ = make_client(gateway, payloads)
    with client:
        response = status(client)
    assert response.status_code == 200
    device = response.json()["data"]["device"]
    assert device["batteryPercent"] is None
    assert device["batteryVoltage"] is None
    assert device["batteryPresent"] is None
    assert device["chargerOnline"] is None
    assert device["batteryState"] in {"unknown", "unavailable"}


def test_unknown_dialog_text_is_not_forwarded(gateway, payloads):
    payloads["/api/v1/dialog/status"]["data"]["state"] = PRIVATE_TEXT
    client, _ = make_client(gateway, payloads)
    with client:
        response = status(client)
    assert response.status_code == 200
    assert response.json()["data"]["dialog"]["state"] == "unavailable"
    assert PRIVATE_TEXT not in response.text


@pytest.mark.parametrize("endpoint,field,value", [
    ("/health", "status", "error"),
    ("/health", "version", PRIVATE_TEXT),
    ("/api/v1/device/status", "connected", "true"),
    ("/api/v1/device/status", "battery_available", 1),
    ("/api/v1/device/status", "battery_percent", -1),
    ("/api/v1/device/status", "battery_percent", 101),
    ("/api/v1/device/status", "battery_percent", True),
    ("/api/v1/device/status", "battery_voltage_mv", 20_001),
    ("/api/v1/device/status", "battery_status", PRIVATE_TEXT),
    ("/api/v1/device/status", "charger_online", "true"),
    ("/api/v1/dialog/status", "session_active", 1),
    ("/api/v1/dialog/status", "can_interrupt", "false"),
    ("/api/v1/dialog/status", "updated_at_ms", -1),
    ("/api/v1/dialog/status", "follow_up_deadline_ms", True),
    ("/api/v1/dialog/status", "stale", "false"),
])
def test_untrusted_status_types_and_ranges_fail_closed(gateway, payloads, endpoint, field, value):
    payloads[endpoint]["data"][field] = value
    client, _ = make_client(gateway, payloads)
    with client:
        response = status(client)
    assert_safe_failure(response)


def test_config_repr_never_contains_credentials(gateway):
    config = make_config(gateway)
    assert API_KEY not in repr(config) and CLIENT_TOKEN not in repr(config)


@pytest.mark.parametrize("url", [
    "https://public.example", "http://8.8.8.8", "http://localhost:8000",
    "http://user:pass@192.168.1.152", "http://192.168.1.152/api/v1",
    "http://192.168.1.152?command=stop", "http://192.168.1.152#fragment",
    "ftp://192.168.1.152", "//192.168.1.152", "http://169.254.169.254",
    "http://[fe80::1]", "http://0.0.0.0", "http://[::]", "http://224.0.0.1",
])
def test_config_rejects_unsafe_upstream_urls(gateway, url):
    with pytest.raises(ValueError):
        gateway.create_app(make_config(gateway, upstream_url=url), transport=httpx.MockTransport(lambda request: httpx.Response(500)))


@pytest.mark.parametrize("change", [
    {"client_token": "short"}, {"client_token": "t" * 257},
    {"client_token": "t" * 25 + "\r\nInjected: bad"},
    {"client_token": "中文" * 24}, {"api_key": ""},
    {"api_key": "k" * 513}, {"api_key": "key\nInjected: bad"},
])
def test_config_rejects_invalid_credentials(gateway, change):
    with pytest.raises(ValueError):
        gateway.create_app(make_config(gateway, **change), transport=httpx.MockTransport(lambda request: httpx.Response(500)))


@pytest.mark.parametrize("url", ["http://127.0.0.1:8000", "http://10.1.2.3", "https://172.16.0.2/", "http://[::1]:8000", "http://[fd00::1]"])
def test_config_allows_explicit_private_and_loopback_roots(gateway, url):
    assert make_config(gateway, upstream_url=url).upstream_url == url


@pytest.mark.parametrize("timeout", [0, -1, True, float("inf"), float("nan"), 10.1])
def test_invalid_timeouts_are_rejected(gateway, timeout):
    with pytest.raises(ValueError):
        make_config(gateway, timeout_seconds=timeout)


def test_total_timeout_applies_even_to_slow_stream_transport(gateway, payloads):
    async def dispatch(request):
        await asyncio.sleep(0.1)
        return httpx.Response(200, json=payloads[request.url.path])

    client, _ = make_client(gateway, payloads, handler=dispatch, timeout_seconds=0.02)
    with client:
        response = status(client)
    assert_safe_failure(response)


def test_outbound_client_explicitly_disables_environment_and_redirects(gateway, payloads, monkeypatch):
    real_client = httpx.AsyncClient
    calls = []

    def tracked_client(*args, **kwargs):
        calls.append(kwargs.copy())
        return real_client(*args, **kwargs)

    monkeypatch.setattr(gateway.httpx, "AsyncClient", tracked_client)
    client, _ = make_client(gateway, payloads)
    with client:
        response = status(client)
    assert response.status_code == 200
    assert calls
    assert all(call.get("trust_env") is False for call in calls)
    assert all(call.get("follow_redirects") is False for call in calls)


def test_fixed_upstream_reads_are_concurrent(gateway, payloads):
    started = set()
    all_started = asyncio.Event()

    async def dispatch(request):
        started.add(request.url.path)
        if started == FIXED_PATHS:
            all_started.set()
        await asyncio.wait_for(all_started.wait(), timeout=0.75)
        return httpx.Response(200, json=payloads[request.url.path])

    client, _ = make_client(gateway, payloads, handler=dispatch)
    with client:
        response = status(client)
    assert response.status_code == 200, response.text
    assert started == FIXED_PATHS


def run_cli(gateway, monkeypatch, key_file, token_file, *, upstream=UPSTREAM, env_file=False):
    calls = []
    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(run=lambda *args, **kwargs: calls.append((args, kwargs))))
    monkeypatch.setattr(sys, "argv", [
        "miniprogram_dev_gateway.py", "--upstream", upstream,
        "--device-env-file" if env_file else "--api-key-file", str(key_file),
        "--expected-serial", SERIAL, "--token-file", str(token_file),
    ])
    gateway.main()
    return calls


def test_cli_rotates_private_token_without_printing_credentials(gateway, tmp_path, monkeypatch, capsys):
    key_file = tmp_path / "device-key"
    key_file.write_text(API_KEY, encoding="ascii")
    key_file.chmod(0o600)
    token_file = tmp_path / "gateway-token"
    token_file.write_text("expired-local-token", encoding="ascii")
    token_file.chmod(0o644)
    calls = run_cli(gateway, monkeypatch, key_file, token_file)
    assert len(calls) == 1
    arguments = calls[0][1]
    assert arguments["host"] == "127.0.0.1" and arguments["port"] == 8765
    assert arguments["access_log"] is False
    token = token_file.read_text(encoding="ascii").strip()
    assert 24 <= len(token) <= 256 and token != "expired-local-token"
    assert token_file.stat().st_mode & 0o777 == 0o600
    assert key_file.read_text(encoding="ascii") == API_KEY
    output = capsys.readouterr()
    assert API_KEY not in output.out + output.err
    assert token not in output.out + output.err


def test_cli_rejects_bad_config_without_overwriting_existing_token(gateway, tmp_path, monkeypatch, capsys):
    key_file = tmp_path / "device-key"
    key_file.write_text(API_KEY, encoding="ascii")
    key_file.chmod(0o600)
    token_file = tmp_path / "gateway-token"
    original = "original-token-must-be-preserved"
    token_file.write_text(original, encoding="ascii")
    token_file.chmod(0o600)
    with pytest.raises(SystemExit) as error:
        run_cli(gateway, monkeypatch, key_file, token_file, upstream=f"http://user:{API_KEY}@192.168.1.152")
    assert error.value.code == 2
    assert token_file.read_text(encoding="ascii") == original
    output = capsys.readouterr()
    assert API_KEY not in output.out + output.err


@pytest.mark.parametrize("alias", ["same_path", "hard_link"])
@pytest.mark.parametrize("env_file", [False, True])
def test_cli_token_cannot_overwrite_device_credentials(gateway, tmp_path, monkeypatch, capsys, alias, env_file):
    key_file = tmp_path / "device.env"
    original = f'AI_CAT_API_KEY="{API_KEY}"\n' if env_file else API_KEY
    key_file.write_text(original, encoding="ascii")
    key_file.chmod(0o600)
    token_file = key_file if alias == "same_path" else tmp_path / "credential-hardlink"
    if alias == "hard_link":
        os.link(key_file, token_file)
    with pytest.raises(SystemExit) as error:
        run_cli(gateway, monkeypatch, key_file, token_file, env_file=env_file)
    assert error.value.code == 2
    assert key_file.read_text(encoding="ascii") == original
    assert token_file.read_text(encoding="ascii") == original
    output = capsys.readouterr()
    assert API_KEY not in output.out + output.err


def test_cli_refuses_symlink_token_without_touching_target(gateway, tmp_path, monkeypatch):
    key_file = tmp_path / "device-key"
    key_file.write_text(API_KEY, encoding="ascii")
    key_file.chmod(0o600)
    token_file = tmp_path / "symlink-token"
    token_file.symlink_to(key_file)
    with pytest.raises(SystemExit) as error:
        run_cli(gateway, monkeypatch, key_file, token_file)
    assert error.value.code == 2
    assert key_file.read_text(encoding="ascii") == API_KEY


def test_cli_rejects_world_readable_key_without_echoing_it(gateway, tmp_path, monkeypatch, capsys):
    key_file = tmp_path / "device-key"
    key_file.write_text(API_KEY, encoding="ascii")
    key_file.chmod(0o644)
    token_file = tmp_path / "gateway-token"
    with pytest.raises(SystemExit) as error:
        run_cli(gateway, monkeypatch, key_file, token_file)
    assert error.value.code == 2
    assert not token_file.exists()
    output = capsys.readouterr()
    assert API_KEY not in output.out + output.err
