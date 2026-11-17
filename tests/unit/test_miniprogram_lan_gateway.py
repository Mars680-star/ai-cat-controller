"""Strict temporary LAN transport; all upstream responses and sockets are local fakes."""
from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import socket
import sys
import threading
import time
from types import SimpleNamespace

import pytest


@pytest.fixture(scope="module")
def proxy():
    source = Path(__file__).resolve().parents[2] / "tools/miniprogram_lan_gateway.py"
    spec = importlib.util.spec_from_file_location("tested_miniprogram_lan_gateway", source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    status = 200

    def __init__(self, raw=b'{"success":true}', headers=None, status=200):
        self.status = status
        self.stream = io.BytesIO(raw)
        self.headers = headers or [("Content-Type", "application/json"), ("Content-Length", str(len(raw)))]

    def getheaders(self):
        return self.headers

    def read(self, size):
        return self.stream.read(size)


@pytest.fixture
def transport(proxy, monkeypatch):
    calls = []
    settings = {"response": FakeResponse()}

    class Connection:
        def __init__(self, host, port, timeout):
            calls.append({"host": host, "port": port, "timeout": timeout})

        def request(self, method, path, body, headers):
            calls[-1].update(method=method, path=path, body=body, headers=headers)

        def getresponse(self):
            value = settings["response"]
            if isinstance(value, Exception):
                raise value
            return value

        def close(self):
            calls[-1]["closed"] = True

    monkeypatch.setattr(proxy.http.client, "HTTPConnection", Connection)
    return calls, settings


def request(proxy, raw, peer=None):
    client, accepted = socket.socketpair()
    client.settimeout(2)
    failures = []

    def run():
        try:
            with accepted:
                proxy.LanHandler(accepted, (peer or proxy.PHONE_IP, 1234), SimpleNamespace())
        except Exception as error:
            failures.append(error)

    worker = threading.Thread(target=run)
    with client:
        client.sendall(raw)
        client.shutdown(socket.SHUT_WR)
        worker.start()
        response = bytearray()
        while True:
            try:
                chunk = client.recv(65536)
            except ConnectionResetError:
                break
            if not chunk:
                break
            response.extend(chunk)
    worker.join(2)
    assert not worker.is_alive() and not failures
    head, body = bytes(response).split(b"\r\n\r\n", 1)
    return int(head.split(b" ", 2)[1]), head, body


def raw_request(proxy, method="GET", path="/health", body=b"", extra=b""):
    return (f"{method} {path} HTTP/1.1\r\nHost: {proxy.LISTEN_IP}:{proxy.LISTEN_PORT}\r\n".encode()
            + extra + f"Content-Length: {len(body)}\r\n\r\n".encode() + body)


def test_only_fixed_pinned_runtime_and_explicit_cli_confirmation(proxy):
    assert (proxy.LISTEN_IP, proxy.LISTEN_PORT) == ("192.168.1.110", 8788)
    assert proxy.PHONE_IP == "192.168.1.109"
    assert (proxy.UPSTREAM_IP, proxy.UPSTREAM_PORT) == ("127.0.0.1", 8787)
    with pytest.raises(SystemExit):
        proxy.parse_args([])
    assert proxy.parse_args(["--confirm-trusted-lan"]).confirm_trusted_lan is True
    for option in ("--host", "--port", "--upstream", "--phone-ip"):
        with pytest.raises(SystemExit):
            proxy.parse_args(["--confirm-trusted-lan", option, "anything"])


@pytest.mark.parametrize("method,path", [
    ("GET", "/health"), ("POST", "/v1/auth/wechat"), ("GET", "/v1/session"),
    ("POST", "/v1/auth/logout"), ("GET", "/v1/pets"),
    *[("GET", "/v1/pets/pet-1/" + suffix) for suffix in (
        "status", "motion", "motion/history", "dashboard", "intimacy", "growth", "dialog",
        "dialog/conversations", "dialog/conversations/since/" + "a" * 64,
        "dialog/conversations/conv-1", "dialog/conversations/conv-1/since/" + "a" * 64,
        "dialog/control", "dialog/requests/request-1", "preferences", "preferences/requests/request-1")],
    *[("POST", "/v1/pets/pet-1/" + suffix) for suffix in (
        "motion/execute", "motion/stop", "interactions", "dialog/commands", "preferences/commands")],
])
def test_exact_live_routes_are_available(proxy, method, path):
    assert proxy.allowed_route(method, path)


@pytest.mark.parametrize("method,path", [
    ("CONNECT", "/health"), ("OPTIONS", "/health"), ("HEAD", "/health"),
    ("GET", "http://127.0.0.1:8787/health"), ("GET", "//health"),
    ("GET", "/health?token=private"), ("GET", "/health#private"), ("GET", "/health/"),
    ("GET", "/api/v1/device/status"), ("POST", "/v1/pets/a/lifecycle/commands"),
    ("GET", "/v1/pets/a/lifecycle"), ("POST", "/v1/pets/a/preferences"),
    ("GET", "/v1/pets/%61/status"), ("GET", "/v1/pets/../status"),
    ("GET", "/v1/pets/a/dialog/conversations/since/bad"),
])
def test_unknown_and_dangerous_routes_never_reach_upstream(proxy, transport, method, path):
    assert not proxy.allowed_route(method, path)
    assert request(proxy, raw_request(proxy, method, path))[0] == 404
    assert transport[0] == []


def test_only_pinned_phone_and_local_health_are_accepted(proxy, transport):
    assert request(proxy, raw_request(proxy), peer="192.168.1.200")[0] == 403
    assert request(proxy, raw_request(proxy, path="/v1/pets"), peer=proxy.LISTEN_IP)[0] == 403
    assert transport[0] == []
    assert request(proxy, raw_request(proxy), peer=proxy.LISTEN_IP)[0] == 200


def test_passes_only_business_bearer_and_json_to_fixed_upstream(proxy, transport):
    payload = json.dumps({"content": "你好"}, ensure_ascii=False).encode()
    extra = (b"Authorization: Bearer business-token_1\r\nContent-Type: application/json; charset=utf-8\r\n"
             b"X-API-Key: DO-NOT-FORWARD\r\nX-Mock-Session: DO-NOT-FORWARD\r\n"
             b"X-Forwarded-For: 127.0.0.1\r\nCookie: DO-NOT-FORWARD\r\n")
    status, headers, body = request(proxy, raw_request(proxy, "POST", "/v1/pets/pet-1/dialog/commands", payload, extra))
    assert status == 200 and json.loads(body) == {"success": True}
    assert b"Cache-Control: no-store" in headers and b"X-Content-Type-Options: nosniff" in headers
    call = transport[0][0]
    assert call["host"] == "127.0.0.1" and call["port"] == 8787 and call["closed"]
    assert call["body"] == payload
    assert call["headers"] == {"Authorization": "Bearer business-token_1", "Content-Type": "application/json",
                               "Accept": "application/json", "Accept-Encoding": "identity"}
    assert b"DO-NOT-FORWARD" not in headers + body


@pytest.mark.parametrize("extra", [
    b"Content-Length: 0\r\n", b"Transfer-Encoding: chunked\r\n", b"Expect: 100-continue\r\n",
    b"Authorization: Bearer a\r\nAuthorization: Bearer b\r\n", b"Host: evil\r\n",
    b"Folded: one\r\n two\r\n", b"Bad : header\r\n", b"x\x00: y\r\n",
    b"Authorization: Basic wrong\r\n", b"Content-Type: application/json\r\nContent-Type: application/json\r\n",
])
def test_ambiguous_framing_and_security_headers_rejected(proxy, transport, extra):
    assert request(proxy, raw_request(proxy, extra=extra))[0] == 400
    assert transport[0] == []


@pytest.mark.parametrize("payload,extra", [
    (b"{}", b""), (b"[]", b"Content-Type: application/json\r\n"),
    (b"not-json", b"Content-Type: application/json\r\n"),
    (b'{"x":NaN}', b"Content-Type: application/json\r\n"),
    (b'{"x":1e999}', b"Content-Type: application/json\r\n"),
    (b'{"x":1,"x":2}', b"Content-Type: application/json\r\n"),
    (b"\xff", b"Content-Type: application/json\r\n"),
])
def test_post_requires_small_finite_json_object(proxy, transport, payload, extra):
    assert request(proxy, raw_request(proxy, "POST", "/v1/auth/wechat", payload, extra))[0] == 400
    assert transport[0] == []


def test_get_body_and_large_headers_and_body_are_rejected(proxy, transport):
    assert request(proxy, raw_request(proxy, body=b"{}"))[0] == 400
    assert request(proxy, raw_request(proxy, extra=b"X-Padding: " + b"a" * 9000 + b"\r\n"))[0] == 431
    assert request(proxy, raw_request(proxy, "POST", "/v1/auth/wechat", b" " * 8193))[0] == 413
    assert transport[0] == []


def test_truncated_body_never_forwarded(proxy, transport):
    raw = (f"POST /v1/auth/wechat HTTP/1.1\r\nHost: {proxy.LISTEN_IP}:{proxy.LISTEN_PORT}\r\n"
           "Content-Type: application/json\r\nContent-Length: 20\r\n\r\n{}").encode()
    assert request(proxy, raw)[0] == 400
    assert transport[0] == []


@pytest.mark.parametrize("response", [
    FakeResponse(status=302, headers=[("Location", "http://evil.example/")]),
    FakeResponse(raw=b"private error text", status=500, headers=[("Content-Type", "text/plain")]),
    FakeResponse(raw=b"[]"), FakeResponse(raw=b"{"), FakeResponse(raw=b"x" * (2 * 1024 * 1024 + 1)),
    FakeResponse(headers=[("Content-Type", "application/json"), ("Content-Encoding", "gzip")]),
    FakeResponse(headers=[("Content-Type", "application/json"), ("Content-Length", "4"), ("Content-Length", "4")]),
    FakeResponse(headers=[("Content-Type", "application/json"), ("Transfer-Encoding", "chunked")]),
    OSError("PRIVATE-ERROR-DO-NOT-PRINT"),
])
def test_upstream_failures_are_bounded_and_sanitized(proxy, transport, response, capsys):
    transport[1]["response"] = response
    status, headers, body = request(proxy, raw_request(proxy))
    assert status == 502 and b"private" not in body and b"Location" not in headers
    assert transport[0][0]["closed"]
    assert capsys.readouterr() == ("", "")


def test_upstream_error_json_and_no_credentials_headers_are_preserved(proxy, transport):
    transport[1]["response"] = FakeResponse(raw=b'{"success":false}', status=401,
        headers=[("Content-Type", "application/json"), ("Set-Cookie", "private"), ("Server", "private")])
    status, headers, _ = request(proxy, raw_request(proxy, path="/v1/pets"))
    assert status == 401 and b"Set-Cookie" not in headers and b"private" not in headers


def test_one_request_per_connection_prevents_pipelined_business_write(proxy, transport):
    raw = raw_request(proxy) + raw_request(proxy, "POST", "/v1/pets/a/motion/execute", b"{}",
                                           b"Content-Type: application/json\r\n")
    assert request(proxy, raw)[0] == 200
    assert len(transport[0]) == 1 and transport[0][0]["method"] == "GET"


def test_connection_slot_is_acquired_before_thread_spawn(proxy, monkeypatch):
    server = object.__new__(proxy.BoundedLanServer)
    server._slots = threading.BoundedSemaphore(1)
    started, closed = [], []
    monkeypatch.setattr(proxy.ThreadingHTTPServer, "process_request", lambda _s, r, a: started.append(r))
    server.shutdown_request = lambda r: closed.append(r)
    server.process_request("first", (proxy.PHONE_IP, 1))
    server.process_request("second", (proxy.PHONE_IP, 2))
    assert started == ["first"] and closed == ["second"]
    assert server.allow_reuse_address is False and server.daemon_threads is True


def test_slot_released_after_handler_and_on_thread_creation_failure(proxy, monkeypatch):
    server = object.__new__(proxy.BoundedLanServer)
    server._slots = threading.BoundedSemaphore(1)
    server._slots.acquire()
    monkeypatch.setattr(proxy.ThreadingHTTPServer, "process_request_thread", lambda *_args: None)
    server.process_request_thread(None, (proxy.PHONE_IP, 1))
    assert server._slots.acquire(blocking=False)
    server._slots.release()

    def fail(*_args):
        raise RuntimeError("fake spawn failure")

    monkeypatch.setattr(proxy.ThreadingHTTPServer, "process_request", fail)
    with pytest.raises(RuntimeError):
        server.process_request(None, (proxy.PHONE_IP, 1))
    assert server._slots.acquire(blocking=False)


def test_windows_binding_is_exclusive(proxy, monkeypatch):
    operations = []
    server = object.__new__(proxy.BoundedLanServer)
    server.socket = SimpleNamespace(setsockopt=lambda *a: operations.append(a))
    monkeypatch.setattr(proxy.os, "name", "nt")
    monkeypatch.setattr(proxy.socket, "SO_EXCLUSIVEADDRUSE", -5, raising=False)
    monkeypatch.setattr(proxy.ThreadingHTTPServer, "server_bind", lambda _s: operations.append("bound"))
    server.server_bind()
    assert operations == [(proxy.socket.SOL_SOCKET, -5, 1), "bound"]


@pytest.mark.parametrize("raw,status", [
    (b"GET /health HTTP/2.0\r\n\r\n", 400),
    (b"GET /" + b"a" * 2050 + b" HTTP/1.1\r\n\r\n", 414),
    (b"GET /health HTTP/1.1\r\nHost: evil.example\r\n\r\n", 400),
    (b"GET /health HTTP/1.1\r\nHost: 192.168.1.110:8788\r\n" + b"X: a\r\n" * 33 + b"\r\n", 431),
    (b"GET /health HTTP/1.1\r\nHost: 192.168.1.110:8788\r\nContent-Length: -1\r\n\r\n", 400),
])
def test_real_socket_request_line_header_and_length_limits(proxy, transport, raw, status):
    assert request(proxy, raw)[0] == status
    assert transport[0] == []


def test_absolute_client_deadline_shuts_down_incomplete_request(proxy, transport, monkeypatch):
    monkeypatch.setattr(proxy, "CLIENT_DEADLINE", 0.04)
    client, accepted = socket.socketpair()
    client.settimeout(1)
    failures = []

    def run():
        try:
            with accepted:
                proxy.LanHandler(accepted, (proxy.PHONE_IP, 1), SimpleNamespace())
        except Exception as error:
            failures.append(error)

    worker = threading.Thread(target=run)
    with client:
        client.sendall(b"GET /health HTTP/1.1\r\nHost:")
        started = time.monotonic()
        worker.start()
        assert client.recv(1024) == b""
    worker.join(1)
    assert not worker.is_alive() and not failures and time.monotonic() - started < 0.8
    assert transport[0] == []


def test_absolute_upstream_deadline_shuts_down_slow_response(proxy, transport, monkeypatch):
    monkeypatch.setattr(proxy, "UPSTREAM_DEADLINE", 0.04)
    stopped = threading.Event()

    class SlowConnection:
        def __init__(self, *_args, **_kwargs):
            self.sock = SimpleNamespace(shutdown=lambda _how: stopped.set())

        def request(self, *_args, **_kwargs):
            pass

        def getresponse(self):
            assert stopped.wait(0.8)
            raise OSError("private upstream timeout details")

        def close(self):
            pass

    monkeypatch.setattr(proxy.http.client, "HTTPConnection", SlowConnection)
    started = time.monotonic()
    assert request(proxy, raw_request(proxy))[0] == 502
    assert stopped.is_set() and time.monotonic() - started < 0.8
