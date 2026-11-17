"""Opt-in, fixed-preset motion with a separate durable at-most-once journal.

No device controller is imported or started here. A timed-out submission is not
retried. An unknown dispatch latches manual review even after a stop ack.
Device idle means ``ended_unverified``, never proof of successful completion.
Unknown delivery latches manual review; even stop acknowledgement cannot prove
that a delayed original command will never arrive. There is no public reset.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import time
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
from ai_cat_controller.local_gateway.delegation import bridge_authorization


_TIMEOUT = 2.5
_PREFLIGHT_TIMEOUT = 3.0
_MAX_BYTES = 64 * 1024
# A short gateway click throttle, not a motor trajectory or command timeout.
# Actual busy state, the upstream scheduler/native lock, and the durable
# pending/unknown journal remain independent exclusion gates.
_COOLDOWN = 0.4
_HISTORY_LIMIT = 20
_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,128}")
_REQUEST = re.compile(r"[A-Za-z0-9._:-]{1,64}")
_SERIAL = re.compile(r"[A-Za-z0-9_-]{6,64}")
_ACTIONS = {
    "head_nod": ("点头", "/motion/head/nod"),
    "head_shake": ("摇头", "/motion/head/shake"),
    "tail_wag": ("开心摇尾", None),
    "proud_pose": ("骄傲转身", None),
    "quiet_companion": ("安静陪伴", None),
    "greeting_combo": ("见面问候", None),
    "celebration_combo": ("升级庆祝", None),
}
_HEAD_ACTIONS = frozenset({"head_nod", "head_shake", "quiet_companion"})
_TAIL_ACTIONS = frozenset(_ACTIONS) - _HEAD_ACTIONS
_ACTIVE = ("sending", "running", "unknown")
Authorize = Callable[[], Awaitable[Any]]


@dataclass(frozen=True)
class _VerifiedDevice:
    busy: bool
    # None is a legacy source that did not provide the field, never a fake True.
    has_tail: bool | None
    motion_enabled: bool | None

    @property
    def action_ids(self) -> frozenset[str]:
        return _HEAD_ACTIONS if self.has_tail is False else frozenset(_ACTIONS)


class MotionError(RuntimeError):
    status_code = 503
    code = "MOTION_UNAVAILABLE"
    message = "动作服务暂不可用，请稍后重试"

    def __init__(self) -> None:
        super().__init__(self.message)


class MotionDisabled(MotionError):
    status_code = 403
    code = "MOTION_DISABLED"
    message = "实机动作尚未开放"


class MotionBusy(MotionError):
    status_code = 409
    code = "MOTION_BUSY"
    message = "设备忙碌或动作冷却中，请稍后重试"


class MotionConflict(MotionError):
    status_code = 409
    code = "MOTION_CONFLICT"
    message = "动作请求冲突，请重新检查"


class MotionUnavailable(MotionError):
    pass


class MotionUnverified(MotionError):
    code = "MOTION_UNVERIFIED"
    message = "设备身份或动作结果尚未验证"


class MotionProductAuthRequired(MotionError):
    code = "MOTION_PRODUCT_AUTH_REQUIRED"
    message = "需关联有效网页授权并刷新动作状态"


class MotionLocked(MotionError):
    status_code = 409
    code = "MOTION_LOCKED"
    message = "当前性格或亲密度尚未解锁该动作"


class MotionUnsupported(MotionError):
    status_code = 409
    code = "MOTION_UNSUPPORTED"
    message = "当前设备不支持该动作"


def _utc(timestamp: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if timestamp is None else timestamp, timezone.utc).isoformat().replace("+00:00", "Z")


def _require(condition: bool) -> None:
    if not condition:
        raise ValueError("unverified motion response")


def _reject_constant(_: str) -> None:
    raise ValueError("non-JSON constant")


def _private_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        address = ipaddress.ip_address(parsed.hostname or "")
        _require(parsed.scheme == "http" and not parsed.username and not parsed.password)
        _require(parsed.path in ("", "/") and "?" not in value and "#" not in value)
        _require(not any(ord(character) <= 32 for character in value))
        # Literal RFC1918/ULA or loopback only; no DNS, redirects or link-local.
        private = address.is_loopback or any(address in network for network in (
            ipaddress.ip_network("10.0.0.0/8"), ipaddress.ip_network("172.16.0.0/12"),
            ipaddress.ip_network("192.168.0.0/16"), ipaddress.ip_network("fc00::/7"),
        ) if address.version == network.version)
        _require(private and not address.is_unspecified and not address.is_multicast)
        if parsed.port is not None:
            _require(1 <= parsed.port <= 65535)
        return value.rstrip("/")
    except Exception:
        pass
    raise ValueError("A literal private device origin is required") from None


class LocalMotionController:
    """One server-configured device; HTTP clients never supply origins or presets."""

    def __init__(
        self, *, db_path: Path, enabled: bool = False, upstream_url: str | None = None,
        api_key: str = "", expected_serial: str | None = None,
        product_session: str | None = None, product_pet_id: str | None = None,
        local_delegation: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if type(enabled) is not bool:
            raise ValueError("Motion enablement must be explicit")
        self.enabled = enabled
        self.expected_serial = expected_serial
        self._url = _private_url(upstream_url) if upstream_url is not None else None
        self._api_key = api_key
        self._transport = transport
        self._product_header, credential = bridge_authorization(upstream_url,product_session,local_delegation,required=False)
        if (credential is None) != (product_pet_id is None):
            raise ValueError("Product session and pet must be configured together")
        if credential is not None and (
            not isinstance(product_pet_id, str)
            or _IDENTIFIER.fullmatch(product_pet_id) is None
        ):
            raise ValueError("Invalid private product authorization")
        self._product_session = credential
        self._product_pet_id = product_pet_id
        self._product_owner: str | None = None
        if expected_serial is not None and (not isinstance(expected_serial, str) or not _SERIAL.fullmatch(expected_serial)):
            raise ValueError("A valid device serial is required")
        if not isinstance(api_key, str) or len(api_key) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in api_key):
            raise ValueError("A valid device credential is required")
        self._configured = self._url is not None and bool(api_key) and expected_serial is not None
        if credential is not None and not self._configured:
            raise ValueError("Product authorization requires a pinned motion device")
        if enabled and not self._configured:
            raise ValueError("Explicit device configuration is required before enabling motion")
        self._tasks: dict[str, asyncio.Task[dict[str, Any]]] = {}
        self._stop_tasks: set[asyncio.Task] = set()
        self._closed = False
        self._lock_descriptor: int | None = None
        self.path = Path(db_path).absolute()
        self._prepare_store()

    def close(self) -> None:
        """Release a quiet controller for shutdown/restart, never mid-submission."""
        if any(not task.done() for task in [*self._tasks.values(), *self._stop_tasks]):
            raise RuntimeError("Motion requests must settle before closing")
        self._closed = True
        descriptor, self._lock_descriptor = self._lock_descriptor, None
        if descriptor is not None:
            os.close(descriptor)

    def __del__(self) -> None:
        descriptor = getattr(self, "_lock_descriptor", None)
        if descriptor is not None:
            os.close(descriptor)

    def _prepare_store(self) -> None:
        try:
            _require(not any(path.is_symlink() for path in (self.path, *self.path.parents)))
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            parent = self.path.parent.stat()
            _require(parent.st_uid == os.getuid() and not parent.st_mode & 0o077)
            for ancestor in self.path.parent.parents:
                info = ancestor.stat()
                _require(info.st_uid in (0, os.getuid()))
                _require(not info.st_mode & 0o022 or (info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)))
            lock_path = Path(str(self.path) + ".lock")
            descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                lock_info = os.fstat(descriptor)
                _require(stat.S_ISREG(lock_info.st_mode) and lock_info.st_uid == os.getuid() and not lock_info.st_mode & 0o077)
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BaseException:
                os.close(descriptor)
                raise
            self._lock_descriptor = descriptor
            created = not self.path.exists()
            if created:
                descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                os.close(descriptor)
            info = self.path.stat()
            _require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077)
            _require(not any(Path(str(self.path) + suffix).exists() for suffix in ("-wal", "-shm")))
            if not created:
                uri = "file:" + quote(str(self.path), safe="/") + "?mode=ro&immutable=1"
                with closing(sqlite3.connect(uri, uri=True)) as connection:
                    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    _require(tables == {"mm_meta", "mm_intents"})
                    _require(connection.execute("SELECT value FROM mm_meta WHERE key='schema'").fetchone() == ("mini_motion_v1",))
            with self._connect() as connection:
                if created:
                    connection.executescript("""
                        CREATE TABLE mm_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                        INSERT INTO mm_meta VALUES('schema','mini_motion_v1');
                        INSERT INTO mm_meta VALUES('cooldown','0');
                        INSERT INTO mm_meta VALUES('manual_review','0');
                        CREATE TABLE mm_intents(
                            seq INTEGER PRIMARY KEY, execution_id TEXT UNIQUE NOT NULL,
                            user_id TEXT NOT NULL, pet_id TEXT NOT NULL, request_id TEXT NOT NULL,
                            action_id TEXT NOT NULL, target_id TEXT, status TEXT NOT NULL,
                            updated_at TEXT NOT NULL,
                            UNIQUE(user_id,pet_id,request_id));
                    """)
                fingerprint = hashlib.sha256((self.expected_serial or "").encode("ascii")).hexdigest()
                stored = connection.execute("SELECT value FROM mm_meta WHERE key='device'").fetchone()
                if self.expected_serial is not None:
                    _require(stored is None or secrets.compare_digest(stored[0], fingerprint))
                    connection.execute("INSERT OR IGNORE INTO mm_meta VALUES('device',?)", (fingerprint,))
                # A process died between committing intent and receiving the ack.
                changed = connection.execute("UPDATE mm_intents SET status='unknown',updated_at=? WHERE status='sending'", (_utc(),)).rowcount
                if changed:
                    connection.execute("UPDATE mm_meta SET value='1' WHERE key='manual_review'")
        except (OSError, sqlite3.Error, ValueError, MotionError):
            pass
        else:
            return
        self.close()
        raise ValueError("A dedicated private motion database is required") from None

    @contextmanager
    def _connect(self):
        if self._closed:
            raise MotionUnavailable()
        try:
            uri = "file:" + quote(str(self.path), safe="/") + "?mode=rw"
            with closing(sqlite3.connect(uri, uri=True, timeout=0.25)) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA synchronous=FULL")
                with connection:
                    yield connection
        except (sqlite3.Error, OSError):
            pass
        else:
            return
        raise MotionUnavailable() from None

    async def _request(self, method: str, path: str, *, body: dict[str, Any] | None = None,
                       expected: int = 200, product: bool = False,
                       expect_list: bool = False) -> Any:
        if product:
            # Never attach the genuine webpage credential to a raw/device path.
            owned_path = "/pets/" + str(self._product_pet_id)
            _require(self._product_session is not None and (
                (method == "GET" and path in ("/pets", owned_path + "/actions"))
                or (method == "POST" and path in {
                    owned_path + "/actions/" + action + "/execute" for action in _ACTIONS
                })
            ))
        if expect_list:
            _require(product and method == "GET")
        async def bounded():
            async with httpx.AsyncClient(transport=self._transport, trust_env=False, follow_redirects=False, timeout=httpx.Timeout(_TIMEOUT)) as client:
                headers = {"Accept": "application/json", "Accept-Encoding": "identity"}
                if path != "/health":
                    headers["X-API-Key"] = self._api_key
                if product:
                    headers[self._product_header] = self._product_session
                elif self._product_header == 'X-Local-Gateway' and path != '/health':
                    headers[self._product_header] = self._product_session
                endpoint = path if path == "/health" else "/api/v1" + path
                async with client.stream(method, self._url + endpoint, headers=headers, json=body) as response:
                    # A scheduler race is possible after the last identity
                    # read (for example an autonomous nod starts). Read a
                    # bounded, complete rejection receipt before deciding it
                    # is an ambiguous dispatch; never treat a bare 409 as
                    # evidence that no command was accepted.
                    rejected = method == "POST" and expected == 202 and response.status_code == 409
                    _require(response.status_code == expected or rejected)
                    _require(response.headers.get("content-encoding", "").lower().strip() in ("", "identity"))
                    length = response.headers.get("content-length")
                    if length is not None:
                        _require(re.fullmatch(r"[0-9]{1,10}", length) is not None and int(length) <= _MAX_BYTES)
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        _require(len(content) + len(chunk) <= _MAX_BYTES)
                        content.extend(chunk)
            payload = json.loads(content.decode("utf-8"), parse_constant=_reject_constant)
            if rejected:
                _require(type(payload) is dict and set(payload) == {"success", "message", "data"}
                         and payload["success"] is False and type(payload["data"]) is dict
                         and set(payload["data"]) == {"code", "details"}
                         and payload["data"]["code"] == "action_conflict"
                         and payload["data"]["details"] == {}
                         and isinstance(payload["message"], str)
                         and (payload["message"] == "动作冷却中，请稍后重试"
                              or re.fullmatch(r"已有动作正在执行: [a-z_]{1,64}", payload["message"]) is not None))
                # These exact MotionService._start rejections happen before
                # scheduling. Do not forward upstream text/identifiers.
                raise MotionBusy()
            _require(type(payload) is dict and payload.get("success") is True
                     and type(payload.get("data")) is (list if expect_list else dict))
            return payload["data"]
        try:
            return await asyncio.wait_for(bounded(), _TIMEOUT)
        except MotionBusy:
            raise
        except Exception:
            pass
        raise MotionUnavailable() from None

    async def _verify(self, *, _policy_required: bool = True) -> _VerifiedDevice:
        try:
            health, device = await asyncio.wait_for(asyncio.gather(
                self._request("GET", "/health"), self._request("GET", "/device/status"),
            ), timeout=_TIMEOUT)
            _require(health.get("status") == "ok" and health.get("adapter") == "local_k1")
            _require(device.get("adapter_mode") == "local_k1" and device.get("connected") is True)
            serial = device.get("device_serial")
            _require(isinstance(serial, str) and secrets.compare_digest(serial, self.expected_serial))
            action = device.get("current_action")
            _require(isinstance(action, str) and re.fullmatch(r"[a-z_]{1,64}", action) is not None and action != "unavailable")
            if _policy_required:
                for field in ("has_tail", "motion_enabled"):
                    _require(field not in device or type(device[field]) is bool)
            # Stop still needs the complete pinned identity above, but malformed
            # execution policy must not disable the independent safety channel.
            has_tail = device.get("has_tail") if type(device.get("has_tail")) is bool else None
            motion_enabled = device.get("motion_enabled") if type(device.get("motion_enabled")) is bool else None
            return _VerifiedDevice(action != "idle", has_tail, motion_enabled)
        except Exception:
            pass
        raise MotionUnverified() from None

    async def _product_policy(self, *, device: _VerifiedDevice | None = None,
                              _defer_topology: bool = False) -> tuple[dict[str, tuple[bool, bool]], str]:
        try:
            policy, owner = await asyncio.wait_for(self._read_product_policy(), _TIMEOUT)
            if not _defer_topology:
                _require(set(policy) == (device.action_ids if device is not None else set(_ACTIONS)))
            return policy, owner
        except Exception:
            pass
        raise MotionProductAuthRequired() from None

    async def _read_product_policy(self) -> tuple[dict[str, tuple[bool, bool]], str]:
        """Read the genuine owner's policy; never log in, claim or elevate a pet.

        Both ownership and the full serial are checked on every policy read.
        Only fixed IDs and strict booleans leave this boundary. Product metadata,
        caller identities and the upstream credential never enter our journal.
        """
        if self._product_session is None:
            raise MotionProductAuthRequired()
        try:
            pets = await self._request("GET", "/pets", product=True, expect_list=True)
            _require(0 < len(pets) <= 128 and all(type(pet) is dict for pet in pets))
            matches = [pet for pet in pets if pet.get("pet_id") == self._product_pet_id]
            _require(len(matches) == 1)
            owner = matches[0].get("owner_user_id")
            _require(isinstance(owner, str) and _IDENTIFIER.fullmatch(owner) is not None)
            serial = matches[0].get("serial_number")
            _require(isinstance(serial, str) and secrets.compare_digest(serial, self.expected_serial))
            _require(matches[0].get("online") is True)
            catalog = await self._request("GET", "/pets/" + self._product_pet_id + "/actions",
                                          product=True, expect_list=True)
            _require(len(catalog) in (len(_HEAD_ACTIONS), len(_ACTIONS)))
            policy: dict[str, tuple[bool, bool]] = {}
            for item in catalog:
                _require(type(item) is dict)
                action_id = item.get("action_id")
                _require(isinstance(action_id, str) and action_id in _ACTIONS and action_id not in policy)
                _require(all(type(item.get(key)) is bool for key in ("available", "unlocked", "debug_unlocked")))
                _require(item.get("parameters_editable") is False
                         and item.get("safety_profile") == "validated_preset_v1")
                policy[action_id] = (item["available"], item["unlocked"] and not item["debug_unlocked"])
            # A three-item candidate is only usable after separately verifying
            # explicit has_tail=False against the full pinned device identity.
            _require(set(policy) == set(_ACTIONS) or set(policy) == _HEAD_ACTIONS)
            _require(self._product_owner is None or secrets.compare_digest(self._product_owner, owner))
            self._product_owner = owner
            return policy, owner
        except Exception:
            pass
        raise MotionProductAuthRequired() from None

    async def _check_policy(self, action_id: str, *, device: _VerifiedDevice) -> str | None:
        self._check_device_action(device, action_id)
        if self._product_session is None:
            if _ACTIONS[action_id][1] is None:
                raise MotionProductAuthRequired()
            return
        policy, owner = await self._product_policy(device=device)
        if action_id not in policy:
            raise MotionUnsupported()
        supported, unlocked = policy[action_id]
        if not supported:
            raise MotionUnsupported()
        if not unlocked:
            raise MotionLocked()
        return owner

    @staticmethod
    def _check_device_action(device: _VerifiedDevice, action_id: str) -> None:
        if device.motion_enabled is False:
            raise MotionDisabled()
        if action_id not in device.action_ids:
            raise MotionUnsupported()

    @staticmethod
    def _product_request_id(user_id: str, pet_id: str, request_id: str) -> str:
        # Product idempotency is scoped to its pet, not the mini-program user.
        # Namespace all three values so different mini owners cannot collide.
        encoded = json.dumps([user_id, pet_id, request_id], separators=(",", ":")).encode("utf-8")
        return "mini_" + hashlib.sha256(encoded).hexdigest()

    def _execution(self, user_id: str | None, pet_id: str | None) -> dict[str, Any] | None:
        if user_id is None or pet_id is None:
            return None
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM mm_intents WHERE user_id=? AND pet_id=? ORDER BY seq DESC LIMIT 1", (user_id, pet_id)).fetchone()
        if row is None:
            return None
        return {"executionId": row["execution_id"], "actionId": row["action_id"], "status": row["status"], "requestId": row["request_id"], "updatedAt": row["updated_at"]}

    async def history(self, *, user_id: str, pet_id: str) -> dict[str, Any]:
        """Read the owner's last fixed number of intents without device I/O.

        Unlike ``snapshot``, this does not reconcile running state against the
        hardware or alter cooldown/manual-review metadata. Stops remain visible
        as separate software receipts; no physical completion is inferred.
        """
        if not all(isinstance(value, str) and _IDENTIFIER.fullmatch(value)
                   for value in (user_id, pet_id)):
            raise MotionConflict()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT execution_id,action_id,status,request_id,updated_at "
                "FROM mm_intents WHERE user_id=? AND pet_id=? "
                "ORDER BY seq DESC LIMIT ?", (user_id, pet_id, _HISTORY_LIMIT),
            ).fetchall()
        return {"items": [{"executionId": row["execution_id"], "actionId": row["action_id"],
                           "status": row["status"], "requestId": row["request_id"],
                           "updatedAt": row["updated_at"]} for row in rows],
                "limit": _HISTORY_LIMIT}

    async def _replay(self, user_id: str, pet_id: str, request_id: str, *, _read_product_policy: bool = True) -> dict[str, Any]:
        result = await self.snapshot(user_id=user_id, pet_id=pet_id, _read_product_policy=_read_product_policy)
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM mm_intents WHERE user_id=? AND pet_id=? AND request_id=?", (user_id, pet_id, request_id)).fetchone()
        if row is not None:
            result["execution"] = {"executionId": row["execution_id"], "actionId": row["action_id"], "status": row["status"], "requestId": row["request_id"], "updatedAt": row["updated_at"]}
        return result

    def _update(self, execution_id: str, state: str, *, only_sending: bool = False) -> None:
        with self._connect() as connection:
            clause = " AND status='sending'" if only_sending else " AND status IN ('sending','running','unknown')"
            changed = connection.execute("UPDATE mm_intents SET status=?,updated_at=? WHERE execution_id=?" + clause, (state, _utc(), execution_id)).rowcount
            if state == "unknown" and changed:
                connection.execute("UPDATE mm_meta SET value='1' WHERE key='manual_review'")

    async def snapshot(self, *, user_id: str | None = None, pet_id: str | None = None,
                       _read_product_policy: bool = True) -> dict[str, Any]:
        verified, device_busy = False, False
        device = None
        policy = None
        if self._configured:
            # Parallel bounded GETs keep a configured-but-expired webpage
            # session from doubling the snapshot latency. Neither sends POSTs.
            reads = [self._verify()]
            if self._product_session is not None and _read_product_policy:
                reads.append(self._product_policy(_defer_topology=True))
            results = await asyncio.gather(*reads, return_exceptions=True)
            if isinstance(results[0], _VerifiedDevice):
                device = results[0]
                device_busy, verified = device.busy, True
            if len(results) > 1 and isinstance(results[1], tuple) and device is not None:
                candidate = results[1][0]
                if set(candidate) == device.action_ids:
                    policy = candidate
        with self._connect() as connection:
            if verified and not device_busy:
                changed = connection.execute("UPDATE mm_intents SET status='ended_unverified',updated_at=? WHERE action_id!='stop' AND status='running'", (_utc(),)).rowcount
                if changed:
                    connection.execute("UPDATE mm_meta SET value=? WHERE key='cooldown'", (str(time.time() + _COOLDOWN),))
            active = connection.execute("SELECT 1 FROM mm_intents WHERE status IN ('sending','running','unknown') LIMIT 1").fetchone() is not None
            manual_review = connection.execute("SELECT value FROM mm_meta WHERE key='manual_review'").fetchone()[0] == "1"
            cooldown = float(connection.execute("SELECT value FROM mm_meta WHERE key='cooldown'").fetchone()[0]) > time.time()
        device_enabled = device.motion_enabled if device is not None else None
        reason = "" if self.enabled and verified and device_enabled is not False and not (active or manual_review or device_busy or cooldown) else (
            "此前请求结果不确定，需现场核查" if manual_review else "尚未开放实机动作" if not self.enabled else "设备身份或状态未验证" if not verified else "设备动作已暂停" if device_enabled is False else "动作正在执行" if active else "设备忙碌" if device_busy else "动作冷却中"
        )
        actions = []
        for key, (name, raw_path) in _ACTIONS.items():
            if device is not None and key not in device.action_ids:
                continue
            policy_reason = ""
            if self._product_session is not None:
                if policy is None:
                    policy_reason = "网页授权或动作目录未验证，请重新关联"
                elif not policy[key][0]:
                    policy_reason = "当前设备不支持该动作"
                elif not policy[key][1]:
                    policy_reason = "当前性格或亲密度尚未解锁"
            elif raw_path is None:
                policy_reason = "需关联网页授权"
            action_reason = reason or policy_reason
            actions.append({"actionId": key, "name": name, "available": action_reason == "", "reason": action_reason})
        return {
            "enabled": self.enabled, "verified": verified, "deviceBusy": device_busy or active or manual_review,
            "hasTail": device.has_tail if device is not None else None,
            "deviceMotionEnabled": device_enabled,
            "actions": actions,
            "execution": self._execution(user_id, pet_id), "sampledAt": _utc(),
        }

    @staticmethod
    def _arguments(user_id: str, pet_id: str, request_id: str, action_id: str, execution_id: str | None = None) -> None:
        valid = all(isinstance(value, str) and _IDENTIFIER.fullmatch(value) for value in (user_id, pet_id))
        valid = valid and isinstance(request_id, str) and _REQUEST.fullmatch(request_id) is not None
        valid = valid and action_id in {*_ACTIONS, "stop"}
        valid = valid and (execution_id is None or (isinstance(execution_id, str) and _IDENTIFIER.fullmatch(execution_id)))
        if not valid:
            raise MotionConflict()

    def _duplicate(self, user_id: str, pet_id: str, request_id: str, action_id: str, target_id: str | None) -> bool:
        with self._connect() as connection:
            row = connection.execute("SELECT action_id,target_id FROM mm_intents WHERE user_id=? AND pet_id=? AND request_id=?", (user_id, pet_id, request_id)).fetchone()
        if row is None:
            return False
        if row["action_id"] != action_id or row["target_id"] != target_id:
            raise MotionConflict()
        return True

    def _reserve(self, user_id: str, pet_id: str, request_id: str, action_id: str, target_id: str | None) -> tuple[str | None, list[str]]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT action_id,target_id FROM mm_intents WHERE user_id=? AND pet_id=? AND request_id=?", (user_id, pet_id, request_id)).fetchone()
            if row:
                if row["action_id"] != action_id or row["target_id"] != target_id:
                    raise MotionConflict()
                return None, []
            pending = connection.execute("SELECT execution_id,action_id,status FROM mm_intents WHERE status IN ('sending','running','unknown')").fetchall()
            if action_id == "stop":
                if any(row["action_id"] == "stop" and row["status"] == "sending" for row in pending):
                    raise MotionBusy()
                if target_id is not None:
                    target = connection.execute("SELECT 1 FROM mm_intents WHERE execution_id=? AND user_id=? AND pet_id=? AND action_id!='stop'", (target_id, user_id, pet_id)).fetchone()
                    if target is None:
                        raise MotionConflict()
                    latest = connection.execute("SELECT execution_id FROM mm_intents WHERE action_id!='stop' ORDER BY seq DESC LIMIT 1").fetchone()
                    if latest is None or latest["execution_id"] != target_id:
                        # An old confirmation cannot stop a later action on the
                        # same hardware, including an action from another owner.
                        raise MotionConflict()
                connection.execute("UPDATE mm_intents SET status='unknown',updated_at=? WHERE status IN ('sending','running')", (_utc(),))
                if any(row["status"] in ("sending", "unknown") for row in pending):
                    connection.execute("UPDATE mm_meta SET value='1' WHERE key='manual_review'")
            else:
                review = connection.execute("SELECT value FROM mm_meta WHERE key='manual_review'").fetchone()[0] == "1"
                if pending or review or float(connection.execute("SELECT value FROM mm_meta WHERE key='cooldown'").fetchone()[0]) > time.time():
                    raise MotionBusy()
            execution = "mx_" + secrets.token_urlsafe(18)
            connection.execute("INSERT INTO mm_intents(execution_id,user_id,pet_id,request_id,action_id,target_id,status,updated_at) VALUES(?,?,?,?,?,?,?,?)", (execution, user_id, pet_id, request_id, action_id, target_id, "sending", _utc()))
            connection.execute("UPDATE mm_meta SET value=? WHERE key='cooldown'", (str(time.time() + _COOLDOWN),))
            return execution, [row["execution_id"] for row in pending]

    async def execute(self, user_id: str, pet_id: str, action_id: str, request_id: str, *, authorize: Authorize) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + _PREFLIGHT_TIMEOUT
        if not self.enabled:
            raise MotionDisabled()
        self._arguments(user_id, pet_id, request_id, action_id)
        if action_id not in _ACTIONS:
            raise MotionConflict()
        if self._duplicate(user_id, pet_id, request_id, action_id, None):
            return await self._replay(user_id, pet_id, request_id)
        try:
            async with asyncio.timeout_at(deadline):
                device = await self._verify()
                self._check_device_action(device, action_id)
                if device.busy:
                    raise MotionBusy()
                await self._check_policy(action_id, device=device)
        except TimeoutError:
            raise MotionUnavailable() from None
        execution, _ = self._reserve(user_id, pet_id, request_id, action_id, None)
        if execution is None:
            return await self._replay(user_id, pet_id, request_id)
        task = asyncio.create_task(self._submit(execution, user_id, pet_id, action_id, request_id, authorize, deadline, device))
        self._tasks[execution] = task
        try:
            return await asyncio.shield(task)
        finally:
            # Caller cancellation must not cancel an intent that may be on wire.
            if task.done():
                self._tasks.pop(execution, None)
            else:
                def completed(future):
                    self._tasks.pop(execution, None)
                    if not future.cancelled():
                        future.exception()  # Consume errors after disconnected caller.
                task.add_done_callback(completed)

    async def _submit(self, execution: str, user_id: str, pet_id: str, action_id: str, request_id: str, authorize: Authorize, deadline: float, device: _VerifiedDevice) -> dict[str, Any]:
        try:
            async with asyncio.timeout_at(deadline):
                allowed = await authorize()
                if allowed is False:
                    raise MotionUnverified()
                product_owner = await self._check_policy(action_id, device=device)
                # Product policy can await multiple HTTP reads. Recheck full
                # physical identity AFTER those reads, adjacent to final auth.
                latest = await self._verify()
                self._check_device_action(latest, action_id)
                if latest.has_tail != device.has_tail or latest.motion_enabled != device.motion_enabled:
                    raise MotionUnverified()
                if latest.busy:
                    raise MotionBusy()
                if await authorize() is False:
                    raise MotionUnverified()
                if asyncio.get_running_loop().time() >= deadline:
                    raise TimeoutError
        except TimeoutError:
            self._update(execution, "failed", only_sending=True)
            raise MotionUnavailable() from None
        except BaseException:
            self._update(execution, "failed", only_sending=True)
            raise
        with self._connect() as connection:
            row = connection.execute("SELECT status FROM mm_intents WHERE execution_id=?", (execution,)).fetchone()
        if row is None or row[0] != "sending":
            return await self.snapshot(user_id=user_id, pet_id=pet_id)  # A priority stop superseded this intent.
        # A synchronous journal read can itself wait for SQLite. Do not let
        # that wait carry a prepared command beyond its pre-dispatch budget.
        if asyncio.get_running_loop().time() >= deadline:
            self._update(execution, "failed", only_sending=True)
            raise MotionUnavailable()
        try:
            if self._product_session is not None:
                upstream_id = self._product_request_id(user_id, pet_id, request_id)
                data = await self._request(
                    "POST", "/pets/" + self._product_pet_id + "/actions/" + action_id + "/execute",
                    body={"request_id": upstream_id}, expected=202, product=True,
                )
                _require(data.get("action_id") == action_id and data.get("request_id") == upstream_id
                         and data.get("status") == "running" and data.get("duplicate") is False)
                _require(isinstance(data.get("execution_id"), str)
                         and _IDENTIFIER.fullmatch(data["execution_id"]) is not None)
                # Current product receipts omit owner/pet fields. If a future
                # version supplies them, they cannot contradict our fixed route
                # and the genuine owner's immediately preceding policy read.
                _require("pet_id" not in data or data["pet_id"] == self._product_pet_id)
                _require(all(key not in data or data[key] == product_owner
                             for key in ("user_id", "owner_user_id")))
            else:
                data = await self._request("POST", _ACTIONS[action_id][1], body={"request_id": request_id}, expected=202)
                _require(set(data) == {"action", "request_id", "state"} and data["action"] == action_id and data["request_id"] == request_id and data["state"] == "running")
        except MotionBusy:
            self._update(execution, "failed", only_sending=True)
            raise
        except asyncio.CancelledError:
            self._update(execution, "unknown", only_sending=True)
            raise
        except Exception:
            self._update(execution, "unknown", only_sending=True)
        else:
            self._update(execution, "running", only_sending=True)
            return await self.snapshot(user_id=user_id, pet_id=pet_id,
                                       _read_product_policy=self._product_session is None)
        raise MotionUnavailable() from None

    async def stop(self, user_id: str, pet_id: str, request_id: str, execution_id: str | None = None, *, authorize: Authorize) -> dict[str, Any]:
        task = asyncio.current_task()
        self._stop_tasks.add(task)
        try:
            return await self._stop(user_id, pet_id, request_id, execution_id, authorize=authorize)
        finally:
            self._stop_tasks.discard(task)

    async def _stop(self, user_id: str, pet_id: str, request_id: str, execution_id: str | None = None, *, authorize: Authorize) -> dict[str, Any]:
        if not self.enabled:
            raise MotionDisabled()
        self._arguments(user_id, pet_id, request_id, "stop", execution_id)
        if self._duplicate(user_id, pet_id, request_id, "stop", execution_id):
            return await self._replay(user_id, pet_id, request_id, _read_product_policy=False)
        await self._verify(_policy_required=False)  # Busy is acceptable; identity remains mandatory.
        if await authorize() is False:
            raise MotionUnverified()
        execution, previous = self._reserve(user_id, pet_id, request_id, "stop", execution_id)
        if execution is None:
            return await self._replay(user_id, pet_id, request_id, _read_product_policy=False)
        # No execute mutex is held during any await. Cancel a local HTTP sender
        # before the stop, rather than allowing its late ack to resurrect state.
        for identifier in previous:
            task = self._tasks.get(identifier)
            if task is not None and not task.done():
                task.cancel()
        try:
            allowed = await authorize()
            if allowed is False:
                raise MotionUnverified()
            await self._verify(_policy_required=False)
            if await authorize() is False:
                raise MotionUnverified()
        except BaseException:
            self._update(execution, "failed", only_sending=True)
            raise
        try:
            data = await self._request("POST", "/motion/stop", body={})
            _require(set(data) == {"stopped", "state"} and type(data["stopped"]) is bool and data["state"] == "idle")
        except asyncio.CancelledError:
            self._update(execution, "unknown", only_sending=True)
            raise
        except Exception:
            self._update(execution, "unknown", only_sending=True)
        else:
            with self._connect() as connection:
                stop_row = connection.execute("SELECT seq,status FROM mm_intents WHERE execution_id=?", (execution,)).fetchone()
                if stop_row["status"] == "sending":
                    connection.execute("UPDATE mm_intents SET status='stopped',updated_at=? WHERE seq<=? AND status IN ('sending','running','unknown')", (_utc(), stop_row["seq"]))
                    connection.execute("UPDATE mm_meta SET value=? WHERE key='cooldown'", (str(time.time() + _COOLDOWN),))
            return await self.snapshot(user_id=user_id, pet_id=pet_id, _read_product_policy=False)
        raise MotionUnavailable() from None
