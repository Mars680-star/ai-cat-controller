"""Read a bounded, verified snapshot from the computer's read-only bridge."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
import math
import re
import secrets
from typing import Any, Protocol

import httpx


BRIDGE_STATUS_URL = "http://127.0.0.1:8765/v1/status"
DEVICE_SHA256_HEADER = "X-AI-Cat-Device-SHA256"
_TOTAL_TIMEOUT_SECONDS = 2.5
_MAX_BYTES = 64 * 1024
_TOKEN = re.compile(r"[A-Za-z0-9._~-]{24,256}")
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?")
_UTC_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z")
_MASKED_SERIAL = re.compile(r"[0-9A-Za-z._:-]{3}\*{4}[0-9A-Za-z._:-]{4}")
_FULL_SERIAL = re.compile(r"[A-Za-z0-9_-]{6,64}")
_BATTERY_STATES = {"charging", "discharging", "full", "not_charging", "unknown", "unavailable"}
_IDLE_STATES = {"ready", "waiting_wake", "interrupted", "echo_guard", "offline", "unavailable"}
_DIALOG_STATES = _IDLE_STATES | {
    "starting", "connecting", "connected", "wake_detected", "listening", "processing_audio", "thinking", "answering",
    "followup_preparing", "proactive_queued", "proactive_speaking", "proactive_finishing", "stopping",
    "followup_listening", "waiting_follow_up", "recovering", "waking", "interrupting", "submitting_text",
}


class StatusUnavailable(RuntimeError):
    """A safe, non-diagnostic status-read failure."""


class StatusReader(Protocol):
    async def read(self) -> dict[str, Any]:
        """Return the verified snapshot data, without its HTTP envelope."""
        ...


def _require(condition: bool) -> None:
    if not condition:
        raise ValueError("unverified bridge status")


def _integer(value: Any, minimum: int, maximum: int) -> bool:
    return type(value) is int and minimum <= value <= maximum


def _nullable_number(value: Any) -> bool:
    return value is None or (type(value) in (int, float) and math.isfinite(value) and value >= 0)


def _reject_constant(value: str) -> None:
    raise ValueError("non-JSON numeric constant")


def _snapshot(envelope: Any) -> dict[str, Any]:
    _require(type(envelope) is dict and envelope.get("success") is True)
    data = envelope["data"]
    _require(type(data) is dict and data["readOnly"] is True)
    health, device, dialog = data["health"], data["device"], data["dialog"]
    _require(all(type(section) is dict for section in (health, device, dialog)))
    _require(health["status"] == "ok" and health["adapter"] == "local_k1")
    version = health["version"]
    _require(isinstance(version, str) and len(version) <= 64 and _VERSION.fullmatch(version) is not None)
    sampled_at = data["sampledAt"]
    _require(isinstance(sampled_at, str) and len(sampled_at) <= 64 and _UTC_TIME.fullmatch(sampled_at) is not None)
    datetime.fromisoformat(sampled_at[:-1] + "+00:00")  # Reject impossible dates, not just malformed strings.

    _require(type(device["connected"]) is bool and device["networkName"] is None and device["lastSeenAt"] is None)
    serial = device["deviceSerial"]
    _require(serial is None or (isinstance(serial, str) and _MASKED_SERIAL.fullmatch(serial) is not None))
    _require(device["batteryPercent"] is None or _integer(device["batteryPercent"], 0, 100))
    _require(device["batteryState"] in _BATTERY_STATES and _nullable_number(device["batteryVoltage"]))
    for key in ("chargerOnline", "batteryPresent"):
        _require(device[key] is None or type(device[key]) is bool)
    if device["batteryState"] == "unavailable":
        _require(all(device[key] is None for key in ("batteryPercent", "batteryVoltage", "chargerOnline", "batteryPresent")))

    state = dialog["state"]
    _require(state in _DIALOG_STATES)
    _require(dialog["elapsedSeconds"] is None if state in _IDLE_STATES else _integer(dialog["elapsedSeconds"], 0, 86400))
    _require(_integer(dialog["followUpRemaining"], 0, 120))
    _require(all(type(dialog[key]) is bool for key in ("canInterrupt", "sessionActive", "stale")))

    # Reconstruct every section. Unknown fields, raw messages and secrets never escape.
    return {
        "readOnly": True,
        "sampledAt": sampled_at,
        "health": {"status": "ok", "version": version, "adapter": "local_k1"},
        "device": {
            "connected": device["connected"], "deviceSerial": serial, "networkName": None,
            "batteryPercent": device["batteryPercent"], "batteryState": device["batteryState"],
            "batteryVoltage": device["batteryVoltage"], "chargerOnline": device["chargerOnline"],
            "batteryPresent": device["batteryPresent"], "lastSeenAt": None,
        },
        "dialog": {
            "state": state, "elapsedSeconds": dialog["elapsedSeconds"],
            "followUpRemaining": dialog["followUpRemaining"], "canInterrupt": dialog["canInterrupt"],
            "sessionActive": dialog["sessionActive"], "stale": dialog["stale"],
        },
    }


@dataclass(frozen=True, slots=True, kw_only=True)
class LocalStatusReader:
    """Server-injected bridge credential; no client-selected URLs or device keys."""

    bridge_token: str = field(repr=False)
    expected_serial: str | None = field(default=None, repr=False)
    transport: httpx.AsyncBaseTransport | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.bridge_token, str) or _TOKEN.fullmatch(self.bridge_token) is None:
            raise ValueError("a valid temporary bridge token is required")
        if self.expected_serial is not None and (not isinstance(self.expected_serial, str) or _FULL_SERIAL.fullmatch(self.expected_serial) is None):
            raise ValueError("a valid expected device serial is required")

    async def read(self) -> dict[str, Any]:
        try:
            return await asyncio.wait_for(self._read_bridge(), timeout=_TOTAL_TIMEOUT_SECONDS)
        except Exception:
            pass
        # Raise outside the handler: even suppressed raw HTTP/JSON context must not survive.
        # asyncio cancellation intentionally propagates instead of being turned into a 503.
        raise StatusUnavailable("Device status is temporarily unavailable") from None

    async def _read_bridge(self) -> dict[str, Any]:
        async with httpx.AsyncClient(
            transport=self.transport,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(_TOTAL_TIMEOUT_SECONDS),
        ) as client:
            async with client.stream(
                "GET", BRIDGE_STATUS_URL,
                headers={"Authorization": "Bearer " + self.bridge_token, "Accept": "application/json", "Accept-Encoding": "identity"},
            ) as response:
                _require(response.status_code == 200)
                if self.expected_serial is not None:
                    fingerprint = response.headers.get(DEVICE_SHA256_HEADER, "")
                    _require(re.fullmatch(r"[0-9A-Fa-f]{64}", fingerprint) is not None)
                    expected = hashlib.sha256(self.expected_serial.encode("ascii")).hexdigest()
                    _require(secrets.compare_digest(fingerprint.lower(), expected))
                _require(response.headers.get("content-encoding", "").strip().lower() in ("", "identity"))
                length = response.headers.get("content-length")
                if length is not None:
                    _require(re.fullmatch(r"[0-9]{1,10}", length) is not None and int(length) <= _MAX_BYTES)
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    _require(len(body) + len(chunk) <= _MAX_BYTES)
                    body.extend(chunk)
        return _snapshot(json.loads(body.decode("utf-8"), parse_constant=_reject_constant))
