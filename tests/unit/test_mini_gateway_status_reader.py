"""Read-only bridge contract tests using in-memory HTTP transports only."""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import traceback

import httpx
import pytest

from ai_cat_controller.mini_gateway import status_reader
from ai_cat_controller.mini_gateway.status_reader import LocalStatusReader, StatusUnavailable


TOKEN = "bridge-test-token-not-real-0123456789"
PRIVATE = "UPSTREAM-PRIVATE-/root/not-a-real-secret.env"
URL = "http://127.0.0.1:8765/v1/status"
SERIAL = "7c2b63fd4a128"
COLLIDING_SERIAL = "7c2another-devicea128"
DEVICE_HEADER = "X-AI-Cat-Device-SHA256"


def snapshot():
    return {
        "success": True,
        "data": {
            "readOnly": True,
            "sampledAt": "2026-10-19T04:05:06.123456Z",
            "health": {"status": "ok", "version": "0.1.0", "adapter": "local_k1"},
            "device": {
                "connected": True, "deviceSerial": "7c2****a128", "networkName": None,
                "batteryPercent": 83, "batteryState": "charging", "batteryVoltage": 4.012,
                "chargerOnline": True, "batteryPresent": True, "lastSeenAt": None,
            },
            "dialog": {
                "state": "thinking", "elapsedSeconds": 8, "followUpRemaining": 0,
                "canInterrupt": True, "sessionActive": True, "stale": False,
            },
        },
    }


def reader_for(payload=None, *, handler=None, headers=None, expected_serial=None):
    if handler is None:
        handler = lambda request: httpx.Response(200, json=payload if payload is not None else snapshot(), headers=headers)
    options = {"bridge_token": TOKEN, "transport": httpx.MockTransport(handler)}
    if expected_serial is not None:
        options["expected_serial"] = expected_serial
    return LocalStatusReader(**options)


def replace(payload, dotted, value):
    target = payload
    keys = dotted.split(".")
    for key in keys[:-1]:
        target = target[key]
    target[keys[-1]] = value


def assert_safe(error):
    assert error.__cause__ is None
    assert error.__context__ is None
    rendered = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    assert TOKEN not in rendered
    assert PRIVATE not in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["processing_audio", "followup_preparing", "connected",
    "proactive_queued", "proactive_speaking", "proactive_finishing", "stopping"])
async def test_native_transition_phases_survive_readonly_bridge_validation(phase):
    payload = snapshot()
    payload["data"]["dialog"]["state"] = phase
    result = await reader_for(payload).read()
    assert result["dialog"]["state"] == phase
    assert result["dialog"]["elapsedSeconds"] == 8
    assert result["readOnly"] is True


@pytest.mark.asyncio
async def test_fixed_get_returns_data_only_and_hides_constructor_token():
    requests = []

    async def dispatch(request):
        requests.append(request)
        return httpx.Response(200, json=snapshot())

    reader = reader_for(handler=dispatch)
    result = await reader.read()
    assert result == snapshot()["data"]
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert str(requests[0].url) == URL
    assert requests[0].headers["Authorization"] == f"Bearer {TOKEN}"
    assert requests[0].content == b""
    assert TOKEN not in repr(reader)
    fields = {field.name: field for field in dataclasses.fields(reader)}
    assert fields["bridge_token"].repr is False


@pytest.mark.asyncio
async def test_http_client_disables_environment_redirects_and_bounds_io(monkeypatch):
    calls = []
    real_client = httpx.AsyncClient

    def tracked(*args, **kwargs):
        calls.append(kwargs)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(status_reader.httpx, "AsyncClient", tracked)
    await reader_for().read()
    assert len(calls) == 1
    assert calls[0]["trust_env"] is False
    assert calls[0]["follow_redirects"] is False
    timeout = calls[0]["timeout"]
    assert all(value is not None and 0 < value <= 2.5 for value in (timeout.connect, timeout.read, timeout.write, timeout.pool))


@pytest.mark.asyncio
async def test_unknown_fields_and_private_messages_are_never_forwarded():
    payload = snapshot()
    payload["message"] = PRIVATE
    payload["data"]["private"] = TOKEN
    for section in ("health", "device", "dialog"):
        payload["data"][section]["private"] = PRIVATE
    try:
        result = await reader_for(payload).read()
    except StatusUnavailable as error:
        assert_safe(error)
    else:
        assert result == snapshot()["data"]
        assert PRIVATE not in json.dumps(result)
        assert TOKEN not in json.dumps(result)


@pytest.mark.parametrize("code", [204, 301, 302, 307, 401, 403, 500])
@pytest.mark.asyncio
async def test_non_200_and_redirects_fail_without_following_or_raw_context(code):
    requests = []

    async def dispatch(request):
        requests.append(request)
        return httpx.Response(code, headers={"Location": "https://example.invalid/private"}, text=PRIVATE)

    with pytest.raises(StatusUnavailable) as caught:
        await reader_for(handler=dispatch).read()
    assert_safe(caught.value)
    assert len(requests) == 1


@pytest.mark.parametrize("path,value", [
    ("success", False), ("success", 1), ("data.readOnly", False),
    ("data.health.status", "error"), ("data.health.adapter", "mock"),
    ("data.health.adapter", "local_k1_fake"), ("data.health.version", PRIVATE),
    ("data.sampledAt", "2026-10-19T04:05:06+00:00"),
    ("data.sampledAt", "2026-02-30T04:05:06Z"), ("data.sampledAt", None),
    ("data.device.connected", 1), ("data.device.deviceSerial", "7c2b63fd4a128"),
    ("data.device.networkName", PRIVATE), ("data.device.lastSeenAt", "2026-10-19T04:05:06Z"),
    ("data.device.batteryPercent", True), ("data.device.batteryPercent", 101),
    ("data.device.batteryPercent", -1), ("data.device.batteryPercent", 2.5),
    ("data.device.batteryVoltage", True), ("data.device.batteryVoltage", -1),
    ("data.device.chargerOnline", 1), ("data.device.batteryPresent", "yes"),
    ("data.device.batteryState", "pretend"),
    ("data.dialog.state", PRIVATE), ("data.dialog.elapsedSeconds", True),
    ("data.dialog.elapsedSeconds", None), ("data.dialog.elapsedSeconds", 86401),
    ("data.dialog.followUpRemaining", True), ("data.dialog.followUpRemaining", 121),
    ("data.dialog.canInterrupt", 1), ("data.dialog.sessionActive", None),
    ("data.dialog.stale", "false"),
])
@pytest.mark.asyncio
async def test_invalid_snapshot_is_rejected_without_coercion(path, value):
    payload = snapshot()
    replace(payload, path, value)
    with pytest.raises(StatusUnavailable) as caught:
        await reader_for(payload).read()
    assert_safe(caught.value)


@pytest.mark.parametrize("key", ["readOnly", "sampledAt", "health", "device", "dialog"])
@pytest.mark.asyncio
async def test_missing_required_section_is_not_fabricated(key):
    payload = snapshot()
    del payload["data"][key]
    with pytest.raises(StatusUnavailable):
        await reader_for(payload).read()


@pytest.mark.asyncio
async def test_unknown_nullable_data_remains_null_and_idle_has_no_fake_elapsed():
    payload = snapshot()
    device = payload["data"]["device"]
    device.update(connected=False, deviceSerial=None, batteryState="unavailable")
    for key in ("batteryPercent", "batteryVoltage", "chargerOnline", "batteryPresent"):
        device[key] = None
    payload["data"]["dialog"].update(state="ready", elapsedSeconds=None, canInterrupt=False, sessionActive=False)
    assert await reader_for(payload).read() == payload["data"]
    payload["data"]["dialog"]["elapsedSeconds"] = 80
    with pytest.raises(StatusUnavailable):
        await reader_for(payload).read()
    payload["data"]["dialog"]["elapsedSeconds"] = None
    device["batteryPercent"] = 0
    with pytest.raises(StatusUnavailable):
        await reader_for(payload).read()


class ByteStream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False
        self.started = False

    async def __aiter__(self):
        self.started = True
        for chunk in self.chunks:
            yield chunk

    async def aclose(self):
        self.closed = True


@pytest.mark.parametrize("extra", [0, 1])
@pytest.mark.asyncio
async def test_64kib_stream_limit_includes_actual_body_not_just_content_length(extra):
    content = json.dumps(snapshot()).encode()
    body = content + b" " * (65536 + extra - len(content))
    stream = ByteStream([body[index:index + 8192] for index in range(0, len(body), 8192)])
    reader = reader_for(handler=lambda request: httpx.Response(200, stream=stream))
    if extra:
        with pytest.raises(StatusUnavailable):
            await reader.read()
    else:
        assert await reader.read() == snapshot()["data"]
    assert stream.closed


@pytest.mark.asyncio
async def test_oversize_declared_body_and_compressed_body_are_rejected_before_read():
    for headers in ({"Content-Length": "65537"}, {"Content-Encoding": "gzip"}):
        stream = ByteStream([b"not read"])
        reader = reader_for(handler=lambda request: httpx.Response(200, headers=headers, stream=stream))
        with pytest.raises(StatusUnavailable):
            await reader.read()
        assert not stream.started
        assert stream.closed


@pytest.mark.parametrize("content", [b"{broken", b"null", b"[]", b'{"success":true,"data":NaN}', b"\xff"])
@pytest.mark.asyncio
async def test_invalid_json_is_sanitized(content):
    with pytest.raises(StatusUnavailable) as caught:
        await reader_for(handler=lambda request: httpx.Response(200, content=content)).read()
    assert_safe(caught.value)


@pytest.mark.asyncio
async def test_transport_error_does_not_survive_as_exception_context():
    async def dispatch(request):
        raise httpx.ReadError(f"{TOKEN} {PRIVATE}", request=request)

    with pytest.raises(StatusUnavailable) as caught:
        await reader_for(handler=dispatch).read()
    assert_safe(caught.value)


@pytest.mark.asyncio
async def test_reader_reuse_does_not_cache_or_share_mutable_snapshots():
    reader = reader_for()
    first = await reader.read()
    first["device"]["batteryPercent"] = 0
    assert await reader.read() == snapshot()["data"]


@pytest.mark.asyncio
async def test_caller_cancellation_is_not_masked_as_an_unavailable_device():
    started = asyncio.Event()

    async def dispatch(request):
        started.set()
        await asyncio.Event().wait()
        return httpx.Response(200, json=snapshot())

    task = asyncio.create_task(reader_for(handler=dispatch).read())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_total_deadline_includes_waiting_for_headers(monkeypatch):
    monkeypatch.setattr(status_reader, "_TOTAL_TIMEOUT_SECONDS", 0.01)
    cancelled = asyncio.Event()

    async def dispatch(request):
        try:
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return httpx.Response(200, json=snapshot())

    with pytest.raises(StatusUnavailable) as caught:
        await reader_for(handler=dispatch).read()
    assert cancelled.is_set()
    assert_safe(caught.value)


@pytest.mark.asyncio
async def test_total_deadline_includes_slow_stream_and_closes_it(monkeypatch):
    monkeypatch.setattr(status_reader, "_TOTAL_TIMEOUT_SECONDS", 0.01)

    class SlowStream(ByteStream):
        async def __aiter__(self):
            yield b"{"
            await asyncio.sleep(1)
            yield b"}"

    stream = SlowStream([])
    with pytest.raises(StatusUnavailable) as caught:
        await reader_for(handler=lambda request: httpx.Response(200, stream=stream)).read()
    assert stream.closed
    assert_safe(caught.value)


@pytest.mark.parametrize("token", [None, "", "short", "x" * 257, TOKEN + "\n"])
def test_invalid_tokens_are_rejected_without_echoing_them(token):
    with pytest.raises(ValueError) as caught:
        LocalStatusReader(bridge_token=token)
    assert TOKEN not in str(caught.value)


def test_url_is_not_an_accepted_constructor_parameter():
    with pytest.raises(TypeError):
        LocalStatusReader(bridge_token=TOKEN, url="http://example.invalid")


@pytest.mark.parametrize("uppercase", [False, True])
@pytest.mark.asyncio
async def test_expected_full_serial_pin_is_verified_without_changing_public_data(uppercase):
    digest = hashlib.sha256(SERIAL.encode("ascii")).hexdigest()
    reader = reader_for(headers={DEVICE_HEADER: digest.upper() if uppercase else digest}, expected_serial=SERIAL)
    assert await reader.read() == snapshot()["data"]
    assert SERIAL not in repr(reader)
    fields = {field.name: field for field in dataclasses.fields(reader)}
    assert fields["expected_serial"].repr is False


@pytest.mark.parametrize("digest", [
    None, "", "a" * 63, "a" * 65, "g" * 64,
    " " + hashlib.sha256(SERIAL.encode("ascii")).hexdigest(),
    hashlib.sha256(SERIAL.encode("ascii")).hexdigest() + " ",
    hashlib.sha256(SERIAL.encode("ascii")).hexdigest() + "," + hashlib.sha256(SERIAL.encode("ascii")).hexdigest(),
    hashlib.sha256(COLLIDING_SERIAL.encode("ascii")).hexdigest(),
    hashlib.sha256(b"7c2****a128").hexdigest(),
])
@pytest.mark.asyncio
async def test_missing_malformed_or_wrong_identity_header_fails_closed(digest):
    headers = {} if digest is None else {DEVICE_HEADER: digest}
    with pytest.raises(StatusUnavailable) as caught:
        await reader_for(headers=headers, expected_serial=SERIAL).read()
    assert_safe(caught.value)
    assert SERIAL not in str(caught.value)


@pytest.mark.asyncio
async def test_identical_masked_serial_cannot_authorize_another_full_serial():
    assert SERIAL[:3] + "****" + SERIAL[-4:] == COLLIDING_SERIAL[:3] + "****" + COLLIDING_SERIAL[-4:]
    digest = hashlib.sha256(SERIAL.encode("ascii")).hexdigest()
    with pytest.raises(StatusUnavailable) as caught:
        await reader_for(headers={DEVICE_HEADER: digest}, expected_serial=COLLIDING_SERIAL).read()
    assert_safe(caught.value)
    assert SERIAL not in repr(caught.value) and COLLIDING_SERIAL not in repr(caught.value)


@pytest.mark.parametrize("headers", [{}, {DEVICE_HEADER: "untrusted-legacy-header"}])
@pytest.mark.asyncio
async def test_unpinned_legacy_reader_does_not_require_a_fingerprint(headers):
    assert await reader_for(headers=headers).read() == snapshot()["data"]


@pytest.mark.parametrize("serial", [True, 123456, b"abcdef", "", "short", "x" * 65, "abcdef:123", "abcdef.123", "设备abcdef", "abcdef\n"])
def test_invalid_expected_serial_is_rejected_without_echoing_it(serial):
    with pytest.raises(ValueError) as caught:
        LocalStatusReader(bridge_token=TOKEN, expected_serial=serial)
    assert TOKEN not in str(caught.value)
    if isinstance(serial, str) and serial:
        assert serial not in str(caught.value)
