"""Loopback-only, read-only bridge for WeChat DevTools. Never run on the K1.

No controller imports/startup, login, DB access, generic proxying or write routes.
Device credentials and the temporary browser credential stay out of the mini app.
Requires the project's development dependencies (httpx).
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import secrets
import tempfile
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
import httpx


MAX_RESPONSE_BYTES = 65_536
DEVICE_SHA256_HEADER = "X-AI-Cat-Device-SHA256"
PRIVATE_NETWORKS = tuple(ipaddress.ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7"))
STATUS_PATHS = ("/health", "/api/v1/device/status", "/api/v1/dialog/status")
BATTERY_STATES = {"charging", "discharging", "full", "not_charging", "unknown", "unavailable"}
DIALOG_STATES = {
    "starting", "connecting", "connected", "ready", "waiting_wake", "wake_detected", "listening",
    "processing_audio", "thinking", "answering", "followup_preparing", "followup_listening", "waiting_follow_up", "interrupted",
    "proactive_queued", "proactive_speaking", "proactive_finishing", "stopping",
    "echo_guard", "recovering", "waking", "interrupting", "submitting_text", "offline", "unavailable",
}
IDLE_STATES = {"ready", "waiting_wake", "interrupted", "echo_guard", "offline", "unavailable"}
UPSTREAM_ERROR = "暂时无法读取AI猫状态，请检查设备连接与电脑代理配置。"


@dataclass(frozen=True)
class GatewayConfig:
    upstream_url: str
    api_key: str = field(repr=False)
    client_token: str = field(repr=False)
    expected_serial: str | None = None
    timeout_seconds: float = 2.5

    def __post_init__(self) -> None:
        try:
            parsed = urlsplit(self.upstream_url)
            address = ipaddress.ip_address(parsed.hostname or "")
            if parsed.scheme not in {"http", "https"} or not (address.is_loopback or any(address in network for network in PRIVATE_NETWORKS if address.version == network.version)):
                raise ValueError
            if address.is_multicast or address.is_unspecified or address.is_reserved and not address.is_loopback:
                raise ValueError
            if parsed.username or parsed.password or "?" in self.upstream_url or "#" in self.upstream_url or parsed.path not in {"", "/"} or any(ord(c) <= 32 for c in self.upstream_url):
                raise ValueError
            if parsed.port is not None and not 1 <= parsed.port <= 65535:
                raise ValueError
            if not isinstance(self.api_key, str) or not 1 <= len(self.api_key) <= 512 or not self.api_key.isascii() or any(ord(c) < 33 or ord(c) == 127 for c in self.api_key):
                raise ValueError
            if not isinstance(self.client_token, str) or not 24 <= len(self.client_token) <= 256 or not re.fullmatch(r"[A-Za-z0-9._~-]+", self.client_token):
                raise ValueError
            if self.expected_serial is not None and not re.fullmatch(r"[A-Za-z0-9_-]{6,64}", self.expected_serial):
                raise ValueError
            if isinstance(self.timeout_seconds, bool) or not math.isfinite(self.timeout_seconds) or not 0 < self.timeout_seconds <= 10:
                raise ValueError
        except (ValueError, TypeError, AttributeError) as exc:
            # Never echo bad URLs or credentials, including through chained errors.
            raise ValueError("Invalid local development gateway configuration") from None


def _integer(value: object, lower: int, upper: int) -> int:
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError("Invalid status value")
    return value


def _boolean(value: object) -> bool:
    if type(value) is not bool:
        raise ValueError("Invalid status value")
    return value


def _nullable_bool(value: object) -> bool | None:
    return None if value is None else _boolean(value)


def _normalize(config: GatewayConfig, health: dict, device: dict, dialog: dict) -> dict:
    if health.get("status") != "ok" or health.get("adapter") != "local_k1" or device.get("adapter_mode") != "local_k1":
        raise ValueError("Real K1 status required")
    version = health.get("version")
    if not isinstance(version, str) or len(version) > 64 or not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?", version):
        raise ValueError("Invalid version")
    serial = device.get("device_serial")
    if config.expected_serial is not None and serial != config.expected_serial:
        raise ValueError("Unexpected device")
    if serial is not None and (not isinstance(serial, str) or not re.fullmatch(r"[A-Za-z0-9_-]{6,64}", serial)):
        raise ValueError("Invalid serial")
    available = _boolean(device.get("battery_available"))
    battery_state = device.get("battery_status")
    if battery_state not in BATTERY_STATES:
        raise ValueError("Invalid battery status")
    percent = device.get("battery_percent")
    voltage = device.get("battery_voltage_mv")
    if percent is not None:
        percent = _integer(percent, 0, 100)
    if voltage is not None:
        voltage = _integer(voltage, 0, 20_000) / 1000
    present = _nullable_bool(device.get("battery_present"))
    charger = _nullable_bool(device.get("charger_online"))
    state = dialog.get("state")
    if not isinstance(state, str):
        raise ValueError("Invalid dialog state")
    known_state = state in DIALOG_STATES
    if not known_state:
        state = "unavailable"
    updated = _integer(dialog.get("updated_at_ms"), 0, 10**15)
    deadline = _integer(dialog.get("follow_up_deadline_ms"), 0, 10**15)
    now = datetime.now(timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    elapsed = min(86_400, max(0, (now_ms - updated) // 1000)) if state not in IDLE_STATES and updated > 0 else None
    remaining = min(120, max(0, math.ceil((deadline - now_ms) / 1000)))
    return {
        "readOnly": True,
        "sampledAt": now.isoformat().replace("+00:00", "Z"),
        "health": {"status": "ok", "version": version, "adapter": "local_k1"},
        "device": {
            "connected": _boolean(device.get("connected")),
            "deviceSerial": serial[:3] + "****" + serial[-4:] if serial else None,
            "networkName": None,
            "batteryPercent": percent if available else None,
            "batteryState": battery_state if available else "unavailable",
            "batteryVoltage": voltage if available else None,
            "chargerOnline": charger if available else None,
            "batteryPresent": present if available else None,
            "lastSeenAt": None,
        },
        "dialog": {
            "state": state, "elapsedSeconds": elapsed, "followUpRemaining": remaining,
            "canInterrupt": _boolean(dialog.get("can_interrupt")),
            "sessionActive": _boolean(dialog.get("session_active")),
            "stale": _boolean(dialog.get("stale")) or not known_state,
        },
    }


def create_app(config: GatewayConfig, *, transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def no_cache(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/health")
    async def gateway_health():
        return {"success": True, "data": {"status": "ok", "readOnly": True}}

    @app.get("/v1/status")
    async def status(request: Request, response: Response):
        expected = "Bearer " + config.client_token
        auth = request.headers.get("authorization", "")
        if not auth.isascii() or not secrets.compare_digest(auth, expected):
            return JSONResponse({"success": False, "message": "本机联调令牌无效，请重新填写。"}, status_code=401)
        if request.url.query or request.headers.get("content-length", "0") != "0" or request.headers.get("transfer-encoding"):
            return JSONResponse({"success": False, "message": "只读接口不接受参数或请求内容。"}, status_code=400)

        async def body_is_empty() -> bool:
            async for chunk in request.stream():
                if chunk:
                    return False
            return True

        try:
            empty = await asyncio.wait_for(body_is_empty(), timeout=min(1, config.timeout_seconds))
        except Exception:
            empty = False
        if not empty:
            return JSONResponse({"success": False, "message": "只读接口不接受参数或请求内容。"}, status_code=400)

        async def read(client: httpx.AsyncClient, path: str) -> dict:
            headers = {"X-API-Key": config.api_key} if path != "/health" else {}
            async with client.stream("GET", config.upstream_url.rstrip("/") + path, headers=headers) as response:
                response.raise_for_status()
                if response.status_code != 200:
                    raise ValueError("Unexpected upstream status")
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > MAX_RESPONSE_BYTES:
                        raise ValueError("Status response too large")
                body = json.loads(raw)
                if not isinstance(body, dict) or body.get("success") is not True or not isinstance(body.get("data"), dict):
                    raise ValueError("Invalid upstream envelope")
                return body["data"]

        try:
            async with httpx.AsyncClient(timeout=config.timeout_seconds, trust_env=False, follow_redirects=False, transport=transport) as client:
                # Overall deadline also bounds connection-pool and slow streaming cases.
                async def sample():
                    health, device, dialog = await asyncio.gather(*(read(client, path) for path in STATUS_PATHS))
                    data = _normalize(config, health, device, dialog)
                    # The normalizer checked this full identity against expected_serial.
                    # Never derive a device fingerprint from its public masked spelling.
                    serial = device.get("device_serial")
                    fingerprint = hashlib.sha256(serial.encode("ascii")).hexdigest() if serial is not None else None
                    return data, fingerprint
                data, fingerprint = await asyncio.wait_for(sample(), timeout=config.timeout_seconds)
            if fingerprint is not None:
                response.headers[DEVICE_SHA256_HEADER] = fingerprint
            return {"success": True, "data": data}
        except Exception:
            # Do not return upstream bodies, exception text, paths, host or credentials.
            return JSONResponse({"success": False, "message": UPSTREAM_ERROR}, status_code=502)

    return app


def _read_key(path: Path, env_file: bool = False) -> str:
    if path.stat().st_mode & 0o077:
        raise ValueError("Credential file must be private (chmod 600)")
    raw = path.read_text(encoding="utf-8").strip()
    if not env_file:
        return raw
    values = [line.split("=", 1)[1].strip().strip("\"'") for line in raw.splitlines() if line.startswith("AI_CAT_API_KEY=")]
    if len(values) != 1:
        raise ValueError("Expected one device credential")
    return values[0]


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only loopback gateway for WeChat DevTools; never a production gateway")
    parser.add_argument("--upstream", required=True, help="Explicit trusted private/loopback device URL")
    key_args = parser.add_mutually_exclusive_group(required=True)
    key_args.add_argument("--api-key-file", type=Path)
    key_args.add_argument("--device-env-file", type=Path)
    parser.add_argument("--expected-serial", required=True)
    parser.add_argument("--token-file", type=Path, required=True, help="Private file, outside the mini-program directory")
    parser.add_argument("--port", type=int, default=8765, choices=[8765])
    args = parser.parse_args()
    try:
        key_path = args.api_key_file or args.device_env_file
        key = _read_key(key_path, env_file=bool(args.device_env_file))
        # Each gateway launch rotates the temporary, read-only client credential.
        token = secrets.token_urlsafe(32)
        config = GatewayConfig(args.upstream, key, token, args.expected_serial)
        token_path = args.token_file.resolve()
        package_root = Path(__file__).resolve().parents[1] / "miniprogram"
        if token_path == key_path.resolve() or package_root == token_path or package_root in token_path.parents:
            raise ValueError("Unsafe credential destination")
        if args.token_file.is_symlink() or (args.token_file.exists() and os.path.samefile(args.token_file, key_path)):
            raise ValueError("Unsafe credential destination")
        # Validate everything before touching an existing token. Atomic replacement
        # never truncates a hard-linked or unrelated credential file.
        descriptor, temporary = tempfile.mkstemp(prefix=".gateway-token-", dir=token_path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="ascii") as handle:
                os.fchmod(handle.fileno(), 0o600)
                handle.write(token + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, token_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    except (OSError, ValueError):
        parser.exit(2, "Invalid or unreadable private gateway configuration; no credentials were printed.\n")
    print("Read-only development gateway: http://127.0.0.1:8765 (not a device connectivity result)", flush=True)
    print("Temporary token written to the private --token-file; never put it in the mini-program package.", flush=True)
    import uvicorn
    uvicorn.run(create_app(config), host="127.0.0.1", port=args.port, access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
