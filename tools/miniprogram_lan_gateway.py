"""Temporary, pinned Windows LAN bridge for an explicitly trusted test network.

Run with Windows Python, not WSL Python. This is HTTP, not production transport:
business login sessions cross the trusted LAN unencrypted. Only the one pinned
phone may use fixed existing mini-gateway routes. AppSecret and device credentials
remain in the existing WSL loopback gateway. No business request is retried here.
The matching firewall rule must also restrict program, interface and phone IP.
"""
from __future__ import annotations

import argparse
import email.message
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
import re
import socket
import threading


LISTEN_IP = "192.168.1.110"
LISTEN_PORT = 8788
PHONE_IP = "192.168.1.109"
UPSTREAM_IP = "127.0.0.1"
UPSTREAM_PORT = 8787
MAX_BODY = 8192
# Saved dialog permits 192 KiB of UTF-8 text. JSON escaping of control
# characters can expand this six-fold, so retain a bounded 2 MiB envelope.
MAX_RESPONSE = 2 * 1024 * 1024
MAX_HEADERS = 8192
MAX_HEADER_COUNT = 32
MAX_CONNECTIONS = 8
CLIENT_DEADLINE = 10.0
UPSTREAM_DEADLINE = 6.0
SOCKET_TIMEOUT = 3.0
_IDENTIFIER = r"[A-Za-z0-9_-]{1,128}"
_REVISION = r"[0-9a-f]{64}"
_PET = r"/v1/pets/" + _IDENTIFIER
_GET = re.compile(
    r"(?:/health|/v1/session|/v1/pets|" + _PET + r"/(?:status|motion(?:/history)?|dashboard|intimacy|growth|"
    r"preferences(?:/requests/" + _IDENTIFIER + r")?|dialog(?:/control|/requests/" + _IDENTIFIER
    + r"|/conversations(?:/since/" + _REVISION + r"|/" + _IDENTIFIER + r"(?:/since/" + _REVISION + r")?)?)?))"
)
_POST = re.compile(r"(?:/v1/auth/(?:wechat|logout)|" + _PET
                   + r"/(?:motion/(?:execute|stop)|interactions|dialog/commands|preferences/commands))")
_HEADER_NAME = re.compile(rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
_BEARER = re.compile(r"Bearer [A-Za-z0-9._~-]{1,512}")


def allowed_route(method: str, path: str) -> bool:
    """No query, URL encoding, redirects, arbitrary targets, or lifecycle routes."""
    pattern = _GET if method == "GET" else _POST if method == "POST" else None
    return bool(pattern and pattern.fullmatch(path))


def _invalid_constant(_value):
    raise ValueError("non-finite JSON")


def _finite_number(raw):
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError("non-finite JSON")
    return value


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _object(raw: bytes):
    value = json.loads(raw.decode("utf-8"), parse_constant=_invalid_constant,
                       parse_float=_finite_number, object_pairs_hook=_unique_object)
    if not isinstance(value, dict):
        raise ValueError("JSON object required")
    return value


def _shutdown_socket(connection):
    try:
        connection.shutdown(socket.SHUT_RDWR)
    except (OSError, AttributeError):
        pass


def _exchange(method, path, body, headers):
    """One fixed request, no redirect following and no retry, even after failure."""
    connection = http.client.HTTPConnection(UPSTREAM_IP, UPSTREAM_PORT, timeout=SOCKET_TIMEOUT)
    captured = []

    def deadline():
        _shutdown_socket(captured[0] if captured else getattr(connection, "sock", None))

    timer = threading.Timer(UPSTREAM_DEADLINE, deadline)
    timer.daemon = True
    timer.start()
    try:
        connection.request(method, path, body=body, headers=headers)
        if getattr(connection, "sock", None) is not None:
            captured.append(connection.sock)
        response = connection.getresponse()
        if not (200 <= response.status < 300 or 400 <= response.status < 600):
            raise ValueError("upstream status rejected")
        values = {}
        total = 0
        for key, value in response.getheaders():
            total += len(key) + len(value) + 4
            name = key.lower()
            if total > MAX_HEADERS or name in values:
                raise ValueError("upstream headers rejected")
            values[name] = value
        if (values.get("content-type", "").lower().split(";", 1)[0].strip() != "application/json"
                or "transfer-encoding" in values
                or values.get("content-encoding", "identity").lower() != "identity"):
            raise ValueError("upstream framing rejected")
        length = values.get("content-length")
        if length is not None and (not re.fullmatch(r"[0-9]{1,9}", length) or int(length) > MAX_RESPONSE):
            raise ValueError("upstream length rejected")
        raw = response.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE or (length is not None and int(length) != len(raw)):
            raise ValueError("upstream body rejected")
        _object(raw)
        return response.status, raw
    finally:
        timer.cancel()
        connection.close()


class LanHandler(BaseHTTPRequestHandler):
    """One bounded HTTP request per socket; never logs paths, credentials or data."""
    protocol_version = "HTTP/1.1"
    server_version = "AI-Cat-LAN"
    sys_version = ""

    def log_message(self, *_args):
        pass

    def log_error(self, *_args):
        pass

    def _reply(self, status, raw=None):
        self.send_response_only(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        raw = raw if raw is not None else b'{"success":false,"message":"LAN request unavailable"}'
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _reject(self, status):
        self._reply(status)
        return False

    def _parse(self):
        # Parse ourselves: stdlib defaults permit ~6 MiB of headers before a
        # handler can inspect them. Here aggregate bytes/count are bounded first.
        line = self.rfile.readline(2049)
        if len(line) > 2048:
            return self._reject(414)
        match = re.fullmatch(rb"([A-Z]{1,12}) ([\x21-\x7e]{1,1800}) HTTP/(1\.[01])\r\n", line)
        if match is None:
            return self._reject(400)
        self.command, self.path, version = (item.decode("ascii") for item in match.groups())
        self.request_version = "HTTP/" + version
        self.requestline = line.decode("ascii").rstrip("\r\n")
        self.headers = email.message.Message()
        total, count = 0, 0
        while True:
            line = self.rfile.readline(MAX_HEADERS + 1)
            total += len(line)
            if total > MAX_HEADERS:
                return self._reject(431)
            if line == b"\r\n":
                break
            count += 1
            if count > MAX_HEADER_COUNT:
                return self._reject(431)
            if not line.endswith(b"\r\n") or b":" not in line:
                return self._reject(400)
            key, value = line[:-2].split(b":", 1)
            if not _HEADER_NAME.fullmatch(key) or any(v < 32 and v != 9 or v == 127 for v in value):
                return self._reject(400)
            self.headers[key.decode("ascii")] = value.decode("latin-1").strip(" \t")
        for key in ("host", "authorization", "content-length", "content-type"):
            if len(self.headers.get_all(key, [])) > 1:
                return self._reject(400)
        if (self.headers.get("Host") != f"{LISTEN_IP}:{LISTEN_PORT}"
                or "Transfer-Encoding" in self.headers or "Expect" in self.headers):
            return self._reject(400)
        if "Authorization" in self.headers and not _BEARER.fullmatch(self.headers["Authorization"]):
            return self._reject(400)
        if self.client_address[0] == LISTEN_IP and not (self.command == "GET" and self.path == "/health"):
            return self._reject(403)
        if not allowed_route(self.command, self.path):
            return self._reject(404)
        return True

    def _dispatch(self):
        if not self._parse():
            return
        length = self.headers.get("Content-Length", "0")
        if not re.fullmatch(r"[0-9]{1,9}", length):
            self._reject(400)
            return
        count = int(length)
        if count > MAX_BODY:
            self._reject(413)
            return
        if self.command == "GET" and count:
            self._reject(400)
            return
        body = self.rfile.read(count)
        if len(body) != count:
            self._reject(400)
            return
        headers = {"Accept": "application/json", "Accept-Encoding": "identity"}
        if self.command == "POST":
            if self.headers.get("Content-Type", "").lower() not in (
                    "application/json", "application/json; charset=utf-8"):
                self._reject(400)
                return
            if body:
                try:
                    _object(body)
                except (ValueError, UnicodeError, RecursionError):
                    self._reject(400)
                    return
            headers["Content-Type"] = "application/json"
        if "Authorization" in self.headers:
            headers["Authorization"] = self.headers["Authorization"]
        try:
            status, raw = _exchange(self.command, self.path, body, headers)
        except (OSError, ValueError, UnicodeError, http.client.HTTPException, RecursionError):
            self._reply(502)
            return
        self._reply(status, raw)

    def handle(self):
        self.close_connection = True
        self.request_version = "HTTP/1.1"
        self.command = ""
        if self.client_address[0] not in (PHONE_IP, LISTEN_IP):
            try:
                self._reply(403)
            except OSError:
                pass
            return
        self.connection.settimeout(SOCKET_TIMEOUT)
        timer = threading.Timer(CLIENT_DEADLINE, _shutdown_socket, args=(self.connection,))
        timer.daemon = True
        timer.start()
        try:
            self._dispatch()
        except (OSError, ValueError, UnicodeError, RecursionError):
            # Includes bounded read/socket timeout; never expose exception text.
            try:
                self._reply(400)
            except OSError:
                pass
        finally:
            timer.cancel()


class BoundedLanServer(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True
    block_on_close = False
    request_queue_size = MAX_CONNECTIONS

    def __init__(self):
        self._slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        super().__init__((LISTEN_IP, LISTEN_PORT), LanHandler)

    def server_bind(self):
        # SO_REUSEADDR on Windows permits another process to share the address.
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def handle_error(self, request, client_address):
        # Base class prints traceback with request data: fail silently instead.
        pass


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Pinned temporary HTTP bridge; trusted LAN only, not production.")
    parser.add_argument("--confirm-trusted-lan", required=True, action="store_true",
                        help="Confirm this temporary LAN and its pinned phone are trusted; sessions use HTTP.")
    return parser.parse_args(argv)


def main(argv=None):
    parse_args(argv)
    try:
        with BoundedLanServer() as server:
            print("Pinned temporary LAN bridge ready; HTTP trusted-LAN testing only.", flush=True)
            try:
                server.serve_forever(poll_interval=0.5)
            except KeyboardInterrupt:
                pass
    except OSError:
        print("LAN bridge unavailable; verify the pinned interface, port and firewall without sharing credentials.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
