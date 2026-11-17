"""Pinned webpage product bridge and separate durable interaction receipts.

Reads never log in, claim, initialize or mutate product state. A recorded intent
is dispatched at most once by this gateway. Unknown dispatches are never retried,
including daily check-in whose upstream key changes at the next local midnight.
"""

from __future__ import annotations

import asyncio
from contextlib import closing
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import time
from typing import Any
from urllib.parse import quote

import httpx

from ai_cat_controller.domain.actions import ACTIONS, action_is_unlocked
from ai_cat_controller.domain.intimacy import INTIMACY_LEVELS, INTERACTION_RULES
from ai_cat_controller.domain import actions as _action_policy, intimacy as _intimacy_policy
from ai_cat_controller.domain.growth import GROWTH_ATTRIBUTE_LABELS
from .motion import _private_url
from .product_models import Dashboard, Growth, IntimacyView, Receipt, validate_public
from ai_cat_controller.local_gateway.delegation import bridge_authorization

_TIMEOUT = 3.0
_READ_TIMEOUT = 4.0
_PREFLIGHT_TIMEOUT = 3.0
_MAX_BYTES = 128 * 1024
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_WRITABLE = {"daily_check_in", "touch", "completed_task", "ignored_greeting"}


def action_requires_tail(action):
    """Use the installed device policy; do not invent a replacement policy."""
    helper = getattr(_action_policy, 'action_requires_tail', None)
    _require(callable(helper))
    return helper(action)


def hardware_unlocks(unlocks, *, has_tail):
    # Original devices without the later optional no-tail metadata retain their
    # original catalogue. Explicit no-tail still requires the real device policy
    # and fails closed if that firmware/source contract is unavailable.
    if has_tail is True:
        return unlocks
    helper = getattr(_intimacy_policy, 'hardware_unlocks', None)
    _require(has_tail is False and callable(helper))
    return helper(unlocks, has_tail=has_tail)


class ProductError(RuntimeError):
    def __init__(self, status=503, code="PRODUCT_UNAVAILABLE", message="真实资料暂不可用，请检查网页授权和设备连接。"):
        super().__init__(message)
        self.status_code = status
        self.code = code


def _require(value):
    if not value:
        raise ValueError("Unverified product response")


def _utc(value=None):
    if value is None:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    _require(isinstance(value, str) and len(value) <= 40)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    _require(parsed.tzinfo is not None)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _reject_constant(_value):
    raise ValueError("Non-JSON number")


class LocalProductService:
    def __init__(self, *, upstream_url: str, api_key: str, expected_serial: str,
                 product_session: str | None = None, product_pet_id: str, db_path: Path,
                 interactions_enabled: bool = False, transport=None, local_delegation: str | None = None):
        self._url = _private_url(upstream_url)
        _require(isinstance(api_key, str) and 1 <= len(api_key) <= 4096
                 and all(33 <= ord(c) <= 126 for c in api_key))
        _require(isinstance(expected_serial, str) and re.fullmatch(r"[A-Za-z0-9_-]{6,64}", expected_serial))
        self._authorization_header, credential = bridge_authorization(upstream_url,product_session,local_delegation)
        _require(isinstance(product_pet_id, str) and _ID.fullmatch(product_pet_id))
        _require(type(interactions_enabled) is bool)
        self.expected_serial = expected_serial
        self.interactions_enabled = interactions_enabled
        self._key, self._session, self._pet = api_key, credential, product_pet_id
        self._owner = None
        self._transport = transport
        self._tasks: dict[tuple[str, str, str], asyncio.Task] = {}
        self._task_events: dict[tuple[str, str, str], str] = {}
        self._lock_descriptor = None
        self._closed = False
        self.path = Path(db_path).absolute()
        self._prepare_store()

    def close(self):
        if any(not task.done() for task in self._tasks.values()):
            raise RuntimeError("Interaction requests must settle before closing")
        self._closed = True
        descriptor, self._lock_descriptor = self._lock_descriptor, None
        if descriptor is not None:
            os.close(descriptor)

    def __del__(self):
        descriptor = getattr(self, "_lock_descriptor", None)
        if descriptor is not None:
            os.close(descriptor)

    def _prepare_store(self):
        try:
            _require(not any(path.is_symlink() for path in (self.path, *self.path.parents)))
            _require(not any(part.lower() in {"miniprogram", "wechat-miniprogram"} for part in self.path.parts))
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            parent = self.path.parent.stat()
            _require(parent.st_uid == os.getuid() and not parent.st_mode & 0o077)
            for ancestor in self.path.parent.parents:
                info = ancestor.stat()
                _require(info.st_uid in (0, os.getuid()))
                _require(not info.st_mode & 0o022 or (info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)))
            lock_path = self.path.with_suffix(self.path.suffix + ".lock")
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            self._lock_descriptor = fd
            info = os.fstat(fd)
            _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.getuid()
                     and not info.st_mode & 0o077)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            created = not self.path.exists()
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                info = os.fstat(fd)
                _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.getuid()
                         and not info.st_mode & 0o077)
            finally:
                os.close(fd)
            _require(not any(Path(str(self.path) + suffix).exists() for suffix in ("-wal", "-shm")))
            fingerprint = hashlib.sha256(json.dumps([self.expected_serial, self._pet], separators=(",", ":")).encode()).hexdigest()
            if not created:
                uri = "file:" + quote(str(self.path), safe="/") + "?mode=ro&immutable=1"
                with closing(sqlite3.connect(uri, uri=True)) as connection:
                    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    _require(tables == {"product_meta", "product_receipts"})
                    _require(connection.execute("SELECT value FROM product_meta WHERE key='schema'").fetchone() == ("mini_product_v1",))
                    value = connection.execute("SELECT value FROM product_meta WHERE key='device'").fetchone()
                    _require(value is not None and secrets.compare_digest(value[0], fingerprint))
            with closing(self._connect()) as connection, connection:
                connection.execute("CREATE TABLE IF NOT EXISTS product_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
                connection.execute("INSERT OR IGNORE INTO product_meta VALUES('schema','mini_product_v1')")
                connection.execute("INSERT OR IGNORE INTO product_meta VALUES('device',?)", (fingerprint,))
                connection.execute("CREATE TABLE IF NOT EXISTS product_receipts (receipt_id TEXT PRIMARY KEY,user_id TEXT NOT NULL,pet_id TEXT NOT NULL,request_id TEXT NOT NULL,event_type TEXT NOT NULL,status TEXT NOT NULL,points_delta INTEGER,points_after INTEGER,duplicate INTEGER,celebration INTEGER,updated_at TEXT NOT NULL,UNIQUE(user_id,pet_id,request_id))")
                connection.execute("UPDATE product_receipts SET status='unknown',updated_at=? WHERE status='sending'", (_utc(),))
        except Exception:
            self.close()
            raise ValueError("Interactions require a separate private available ledger") from None

    def _connect(self):
        if self._closed:
            raise ProductError()
        connection = sqlite3.connect(self.path, timeout=1)
        connection.row_factory = sqlite3.Row
        return connection

    async def _request(self, method, path, *, body=None, expect_list=False, dispatched=None, deadline=None):
        owned = "/pets/" + self._pet
        _require((method == "GET" and path in {"/health", "/device/status", "/pets", owned + "/dashboard", owned + "/intimacy", owned + "/growth"})
                 or (method == "POST" and path == owned + "/interactions"))
        product = path.startswith("/pets")

        async def read():
            async with httpx.AsyncClient(transport=self._transport, trust_env=False, follow_redirects=False,
                                         timeout=httpx.Timeout(_TIMEOUT)) as client:
                headers = {"Accept": "application/json", "Accept-Encoding": "identity"}
                if path != "/health":
                    headers["X-API-Key"] = self._key
                if product:
                    headers[self._authorization_header] = self._session
                elif self._authorization_header == 'X-Local-Gateway' and path != '/health':
                    headers[self._authorization_header] = self._session
                endpoint = path if path == "/health" else "/api/v1" + path
                if deadline is not None:
                    _require(time.monotonic() < deadline)
                if dispatched is not None:
                    dispatched[0] = True
                async with client.stream(method, self._url + endpoint, headers=headers, json=body) as response:
                    _require(response.status_code == 200)
                    _require(response.headers.get("content-encoding", "").lower().strip() in {"", "identity"})
                    size = response.headers.get("content-length")
                    if size is not None:
                        _require(re.fullmatch(r"[0-9]{1,10}", size) and int(size) <= _MAX_BYTES)
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        _require(len(raw) + len(chunk) <= _MAX_BYTES)
                        raw.extend(chunk)
            payload = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
            _require(type(payload) is dict and payload.get("success") is True
                     and type(payload.get("data")) is (list if expect_list else dict))
            return payload["data"]

        return await asyncio.wait_for(read(), _TIMEOUT)

    async def _verify(self, *, with_policy=False):
        """Genuine owner plus full registered serial and physical adapter, each read."""
        health, device, pets = await asyncio.gather(
            self._request("GET", "/health"), self._request("GET", "/device/status"),
            self._request("GET", "/pets", expect_list=True),
        )
        _require(health.get("status") == "ok" and health.get("adapter") == "local_k1")
        _require(device.get("adapter_mode") == "local_k1" and device.get("connected") is True)
        serial = device.get("device_serial")
        _require(isinstance(serial, str) and secrets.compare_digest(serial, self.expected_serial))
        # Missing fields are the original device contract; an explicit false
        # must never be discarded and a malformed policy is not a true value.
        has_tail = device.get("has_tail", True)
        motion_enabled = device.get("motion_enabled", True)
        _require(type(has_tail) is bool and type(motion_enabled) is bool)
        _require(0 < len(pets) <= 128 and all(type(pet) is dict for pet in pets))
        owned = [pet for pet in pets if pet.get("pet_id") == self._pet]
        _require(len(owned) == 1)
        owner, serial = owned[0].get("owner_user_id"), owned[0].get("serial_number")
        _require(isinstance(owner, str) and _ID.fullmatch(owner))
        _require(isinstance(serial, str) and secrets.compare_digest(serial, self.expected_serial))
        _require(owned[0].get("online") is True)
        _require(self._owner is None or secrets.compare_digest(owner, self._owner))
        fingerprint = hashlib.sha256(owner.encode()).hexdigest()
        with closing(self._connect()) as connection, connection:
            previous = connection.execute("SELECT value FROM product_meta WHERE key='owner'").fetchone()
            _require(previous is None or secrets.compare_digest(previous[0], fingerprint))
            connection.execute("INSERT OR IGNORE INTO product_meta VALUES('owner',?)", (fingerprint,))
        self._owner = owner
        return (owner, has_tail, motion_enabled) if with_policy else owner

    @staticmethod
    def _intimacy(raw, *, has_tail=True):
        _require(type(raw) is dict and type(raw.get("level")) is dict and type(raw.get("progress")) is dict)
        level = raw["level"]
        index = level["level"]
        _require(type(index) is int and 0 <= index <= 4)
        return {"points": raw["points"], "level": index, "levelName": level["name"],
                "minPoints": level["min_points"], "nextPoints": level["next_points"],
                "progressPercent": raw["progress"]["percent"], "address": raw["address"],
                "currentUnlocks": list(hardware_unlocks(INTIMACY_LEVELS[index].unlocks, has_tail=has_tail)),
                "nextUnlocks": list(hardware_unlocks(INTIMACY_LEVELS[index + 1].unlocks, has_tail=has_tail)) if index < 4 else []}

    @staticmethod
    def _history(raw):
        _require(type(raw) is list and len(raw) <= 30)
        return [{"eventType": item["event_type"], "label": INTERACTION_RULES[item["event_type"]].label,
                 "pointsDelta": item["points_delta"], "pointsAfter": item["points_after"],
                 "createdAt": _utc(item["created_at"])} for item in raw]

    async def read(self, operation: str):
        if self._closed:
            raise ProductError()
        try:
            return await asyncio.wait_for(self._read(operation), _READ_TIMEOUT)
        except Exception:
            raise ProductError() from None

    async def _read(self, operation: str):
        _require(operation in {"dashboard", "intimacy", "growth"})
        try:
            before, has_tail, motion_enabled = await self._verify(with_policy=True)
            base = "/pets/" + self._pet
            raw = await self._request("GET", base + "/" + operation)
            if operation == "dashboard":
                intimacy = await self._request("GET", base + "/intimacy")
                _require(raw["pet"].get("pet_id") == self._pet and raw["pet"].get("owner_user_id") == before)
                personality = raw["personality"]
                data = {"petName": raw["pet"]["name"], "personality": {
                    "personalityId": personality["personality_id"], "name": personality["name"],
                    "description": personality["description"], "languageStyle": personality["language_style"],
                    "voiceName": personality["voice_name"]}, "intimacy": self._intimacy(raw["intimacy"], has_tail=has_tail),
                    "unlockedActionIds": [action.action_id for action in ACTIONS if (has_tail or not action_requires_tail(action)) and action_is_unlocked(
                        action, personality["personality_id"], raw["intimacy"]["level"]["level"])],
                    "recentInteractions": self._history(intimacy["history"]), "historyScope": "latest_30",
                    "today": {"interactionCount": None, "pointsGained": None},
                    "interactionsEnabled": self.interactions_enabled and intimacy.get("debug_unlimited_touch_intimacy") is False,
                    "sampledAt": _utc()}
                result = validate_public(Dashboard, data)
            elif operation == "intimacy":
                result = validate_public(IntimacyView, {"intimacy": self._intimacy(raw, has_tail=has_tail),
                    "dailyGrowthCap": raw["daily_growth_cap"], "dailyCheckInCompleted": raw["daily_check_in_completed"],
                    "rules": [{"eventType": item["event_type"], "label": item["label"], "points": item["points"],
                               "maxPerDay": item["max_per_day"]} for item in raw["rules"]],
                    "history": self._history(raw["history"]), "historyScope": "latest_30",
                    "interactionsEnabled": self.interactions_enabled and raw.get("debug_unlimited_touch_intimacy") is False,
                    "sampledAt": _utc()})
            else:
                profile = raw["behavior_profile"]
                tags = [{"tagId": item["tag_id"], "name": item["display_name"], "description": item["description"],
                         "earnedAt": _utc(item["earned_at"]), "active": item["active"],
                         "replacedAt": _utc(item["replaced_at"]) if item["replaced_at"] is not None else None}
                        for item in raw["tag_history"]]
                result = validate_public(Growth, {"enabled": raw["enabled"],
                    "attributes": [{"attributeId": key, "label": item["label"], "tendency": item["tendency"],
                                    "value": item["value"]} for key, item in raw["attributes"].items()],
                    "activeTags": [{key: value for key, value in tag.items() if key not in {"active", "replacedAt"}}
                                   for tag in tags if tag["active"]], "tagHistory": tags,
                    "recentEvents": [{"eventType": item["event_type"], "label": item["event_label"], "topic": item["topic"],
                                      "emotion": item["emotion"], "changedAttributes": [
                                          {label: attribute.value for attribute, label in GROWTH_ATTRIBUTE_LABELS.items()}[label]
                                          for label in item["changed_attributes"]],
                                      "createdAt": _utc(item["created_at"])} for item in raw["recent_events"]],
                    "behavior": {"basePersonalityId": profile["base_personality_id"],
                                 "basePersonalityName": profile["base_personality_name"],
                                 "intimacyLevel": profile["intimacy_level"], "activeTagNames": profile["active_tag_names"],
                                 "directives": profile["directives"]}, "sampledAt": _utc()})
                _require({item["attributeId"] for item in result["attributes"]} == {
                    "curiosity", "empathy", "knowledge", "energy", "mischief", "discipline"})
            after, after_tail, after_motion = await self._verify(with_policy=True)
            _require(secrets.compare_digest(before, after) and (has_tail, motion_enabled) == (after_tail, after_motion))
            return result
        except Exception:
            raise ProductError() from None

    def _find(self, user, pet, request):
        with closing(self._connect()) as connection:
            return connection.execute("SELECT * FROM product_receipts WHERE user_id=? AND pet_id=? AND request_id=?",
                                      (user, pet, request)).fetchone()

    @staticmethod
    def _receipt(row):
        return validate_public(Receipt, {"receiptId": row["receipt_id"], "eventType": row["event_type"],
            "requestId": row["request_id"], "status": row["status"], "pointsDelta": row["points_delta"],
            "pointsAfter": row["points_after"], "duplicate": bool(row["duplicate"]) if row["duplicate"] is not None else None,
            "celebrationScheduled": bool(row["celebration"]) if row["celebration"] is not None else None,
            "updatedAt": row["updated_at"]})

    async def interact(self, *, user_id, pet_id, event_type, request_id, authorize):
        if not self.interactions_enabled or self._closed:
            raise ProductError(403, "INTERACTIONS_DISABLED", "真实培养互动尚未开放。")
        _require(_ID.fullmatch(user_id) and _ID.fullmatch(pet_id) and event_type in _WRITABLE
                 and re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", request_id))
        key = user_id, pet_id, request_id
        if key in self._tasks:
            if self._task_events[key] != event_type:
                raise ProductError(409, "INTERACTION_CONFLICT", "同一请求编号不能用于不同互动。")
            return await asyncio.shield(self._tasks[key])
        existing = self._find(user_id, pet_id, request_id)
        if existing is not None:
            if existing["event_type"] != event_type:
                raise ProductError(409, "INTERACTION_CONFLICT", "同一请求编号不能用于不同互动。")
            return self._receipt(existing)
        with closing(self._connect()) as connection:
            # This ledger is pinned to one physical device and product pet.
            # An unresolved write remains unsafe after mini-owner reassignment.
            # Only receipt retrieval above is scoped to the current mini user.
            blocked = connection.execute("SELECT 1 FROM product_receipts WHERE status IN ('unknown','sending') LIMIT 1").fetchone()
        if blocked is not None:
            raise ProductError(409, "INTERACTION_REVIEW_REQUIRED", "上一条培养互动结果待核验，请保留原请求编号，不要重新提交。")
        # A single outstanding interaction per pinned device prevents button
        # bursts; callers explicitly retry their original ID, never auto-replay.
        if any(not task.done() for task in self._tasks.values()):
            raise ProductError(409, "INTERACTION_BUSY", "上一条培养互动尚在处理，请稍后刷新。")
        task = asyncio.create_task(self._submit(user_id, pet_id, event_type, request_id, authorize))
        self._tasks[key] = task
        self._task_events[key] = event_type
        def done(future):
            self._tasks.pop(key, None)
            self._task_events.pop(key, None)
            if not future.cancelled():
                future.exception()
        task.add_done_callback(done)
        return await asyncio.shield(task)

    async def _submit(self, user, pet, event, request, authorize):
        deadline = time.monotonic() + _PREFLIGHT_TIMEOUT
        async def interaction_policy():
            raw = await self._request("GET", "/pets/" + self._pet + "/intimacy")
            if raw.get("debug_unlimited_touch_intimacy") is not False:
                raise ProductError(403, "INTERACTION_POLICY_UNVERIFIED", "当前培养规则未验证，真实互动暂不可用。")
        async def verify_safe():
            try:
                await self._verify()
            except Exception:
                raise ProductError() from None
        async def preflight():
            await authorize()
            await verify_safe()
            await interaction_policy()
            await authorize()
        try:
            await asyncio.wait_for(preflight(), max(0.001, deadline - time.monotonic()))
        except TimeoutError:
            raise ProductError() from None
        receipt_id = "interaction_" + secrets.token_urlsafe(18)
        with closing(self._connect()) as connection, connection:
            connection.execute("INSERT INTO product_receipts(receipt_id,user_id,pet_id,request_id,event_type,status,updated_at) VALUES(?,?,?,?,?,'sending',?)",
                               (receipt_id, user, pet, request, event, _utc()))
        dispatched = [False]
        result = None
        try:
            # Re-verify physical pin, actual webpage owner and mini ownership at
            # the submission boundary, not merely when rendering the page.
            async def adjacent_check():
                await self._verify()
                await interaction_policy()
                await authorize()
            await asyncio.wait_for(adjacent_check(), max(0.001, deadline - time.monotonic()))
            _require(time.monotonic() < deadline)
            upstream_id = "mi_" + hashlib.sha256(json.dumps(
                [user, pet, request], separators=(",", ":")).encode()).hexdigest()
            raw = await self._request("POST", "/pets/" + self._pet + "/interactions",
                                      body={"event_type": event, "request_id": upstream_id, "metadata": {}},
                                      dispatched=dispatched, deadline=deadline)
            _require(raw.get("event_type") == event and type(raw.get("duplicate")) is bool)
            # Daily check-in uses a server-local-date canonical ID, deliberately
            # unlike all other upstream IDs. No retry may cross that boundary.
            if event != "daily_check_in":
                _require(raw.get("request_id") == upstream_id)
            else:
                _require(isinstance(raw.get("request_id"), str) and re.fullmatch(
                    r"daily-check-in:" + re.escape(self._pet) + r":\d{4}-\d{2}-\d{2}", raw["request_id"]))
            celebration = raw.get("level_up", {}).get("celebration_scheduled", False)
            _require(type(celebration) is bool)
            result = validate_public(Receipt, {"receiptId": receipt_id, "eventType": event, "requestId": request,
                "status": "recorded", "pointsDelta": raw["points_delta"], "pointsAfter": raw["points_after"],
                "duplicate": raw["duplicate"], "celebrationScheduled": celebration, "updatedAt": _utc()})
            await self._verify()
        except BaseException as error:
            # Even a 4xx/malformed receipt might follow a committed mutation.
            # Unknown delivery is a permanent no-resend receipt, never a retry.
            with closing(self._connect()) as connection, connection:
                connection.execute("UPDATE product_receipts SET status=?,updated_at=? WHERE receipt_id=?",
                                   ("unknown" if dispatched[0] else "failed", _utc(), receipt_id))
            if isinstance(error, Exception) and dispatched[0]:
                return self._receipt(self._find(user, pet, request))
            raise
        with closing(self._connect()) as connection, connection:
            connection.execute("UPDATE product_receipts SET status='recorded',points_delta=?,points_after=?,duplicate=?,celebration=?,updated_at=? WHERE receipt_id=?",
                               (result["pointsDelta"], result["pointsAfter"], result["duplicate"], result["celebrationScheduled"], result["updatedAt"], receipt_id))
        return result
