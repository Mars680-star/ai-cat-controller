"""Opt-in ordinary settings and device-local feedback; no destructive routes.

The upstream settings API has no idempotency key and its two patches are not
atomic. Each dispatch therefore has a durable intent, bounded acknowledgement,
and an honest per-step receipt. Neither an unknown write nor a duplicate ID is
ever retried. Feedback text and names are kept in memory, not in this ledger.
"""

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
from urllib.parse import quote

import httpx

from .dialog import LocalDialogReader
from .motion import _private_url, _utc
from .preferences_models import Command, Receipt, Snapshot, validate_preferences, visible

_PREFLIGHT_TIMEOUT = 3.0
_READ_TIMEOUT = 4.0
_ACK_TIMEOUT = 10.0
_MAX_BYTES = 128 * 1024
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_SCHEMA = {
    "pf_meta": [("key", "TEXT", 0, 1), ("value", "TEXT", 1, 0)],
    "pf_requests": [("user_id", "TEXT", 1, 1), ("pet_id", "TEXT", 1, 2),
        ("request_id", "TEXT", 1, 3), ("kind", "TEXT", 1, 0), ("digest", "TEXT", 1, 0),
        ("status", "TEXT", 1, 0), ("updated_at", "TEXT", 1, 0)],
    "pf_steps": [("user_id", "TEXT", 1, 1), ("pet_id", "TEXT", 1, 2),
        ("request_id", "TEXT", 1, 3), ("step", "TEXT", 1, 4), ("ordinal", "INTEGER", 1, 0),
        ("status", "TEXT", 1, 0)],
}


class PreferencesError(RuntimeError):
    def __init__(self, status=503, code="PREFERENCES_UNAVAILABLE", message="设置与反馈暂不可用，请检查授权和连接。"):
        super().__init__(message)
        self.status_code, self.code = status, code


def _require(value):
    if not value:
        raise ValueError("Unverified preferences")


class LocalPreferencesService:
    def __init__(self, *, db_path, settings_enabled=False, feedback_enabled=False,
                 upstream_url, api_key, expected_serial, product_session=None, product_pet_id, transport=None, local_delegation=None):
        _require(type(settings_enabled) is bool and type(feedback_enabled) is bool)
        # Reuse its strict fixed-origin/private configuration validation only;
        # never enable or call persisted-dialog capability/history endpoints.
        identity = LocalDialogReader(upstream_url=upstream_url, api_key=api_key,
            expected_serial=expected_serial, product_session=product_session,
            product_pet_id=product_pet_id, transport=transport, local_delegation=local_delegation)
        identity.close()
        self._url = _private_url(upstream_url)
        self._key, self._session, self._pet = api_key, identity._session, product_pet_id
        self._authorization_header = identity._authorization_header
        self.expected_serial = expected_serial
        self.settings_enabled, self.feedback_enabled = settings_enabled, feedback_enabled
        self._transport, self._closed, self._lock_descriptor = transport, False, None
        self._tasks, self._signatures, self._lanes = {}, {}, {}
        self.path = Path(db_path).absolute()
        self._prepare_store()

    def close(self):
        if any(not task.done() for task in self._tasks.values()):
            raise RuntimeError("Preference submissions must settle before closing")
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
            info = self.path.parent.stat()
            _require(info.st_uid == os.getuid() and not info.st_mode & 0o077)
            for parent in self.path.parent.parents:
                info = parent.stat()
                _require(info.st_uid in (0, os.getuid()) and
                    (not info.st_mode & 0o022 or info.st_uid == 0 and info.st_mode & stat.S_ISVTX))
            descriptor = os.open(str(self.path) + ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            self._lock_descriptor = descriptor
            info = os.fstat(descriptor)
            _require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1 and not info.st_mode & 0o077)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            created = not self.path.exists()
            descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                info = os.fstat(descriptor)
                _require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1 and not info.st_mode & 0o077)
            finally:
                os.close(descriptor)
            _require(not any(Path(str(self.path) + suffix).exists() for suffix in ("-wal", "-shm")))
            pin = hashlib.sha256(json.dumps([self._url, self.expected_serial, self._pet], separators=(",", ":")).encode()).hexdigest()
            if not created:
                uri = "file:" + quote(str(self.path), safe="/") + "?mode=ro&immutable=1"
                with closing(sqlite3.connect(uri, uri=True)) as connection:
                    _require({row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")} == set(_SCHEMA))
                    _require(not connection.execute("SELECT 1 FROM sqlite_master WHERE type IN ('trigger','view')").fetchone())
                    for table, expected in _SCHEMA.items():
                        _require([(row[1], row[2], row[3], row[5]) for row in connection.execute("PRAGMA table_info(" + table + ")")] == expected)
                    _require(connection.execute("SELECT value FROM pf_meta WHERE key='schema'").fetchone() == ("preferences_v1",))
                    _require(connection.execute("SELECT value FROM pf_meta WHERE key='pin'").fetchone() == (pin,))
            with closing(self._connect()) as connection, connection:
                if created:
                    connection.executescript("""
                        CREATE TABLE pf_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                        CREATE TABLE pf_requests(user_id TEXT NOT NULL,pet_id TEXT NOT NULL,
                            request_id TEXT NOT NULL,kind TEXT NOT NULL,digest TEXT NOT NULL,
                            status TEXT NOT NULL,updated_at TEXT NOT NULL,
                            PRIMARY KEY(user_id,pet_id,request_id));
                        CREATE TABLE pf_steps(user_id TEXT NOT NULL,pet_id TEXT NOT NULL,
                            request_id TEXT NOT NULL,step TEXT NOT NULL,ordinal INTEGER NOT NULL,
                            status TEXT NOT NULL,PRIMARY KEY(user_id,pet_id,request_id,step));
                    """)
                    connection.executemany("INSERT INTO pf_meta VALUES(?,?)", [("schema", "preferences_v1"), ("pin", pin),
                        ("review_settings", "0"), ("review_feedback", "0")])
                for row in connection.execute("SELECT * FROM pf_requests WHERE status='sending'").fetchall():
                    key = row["user_id"], row["pet_id"], row["request_id"]
                    connection.execute("UPDATE pf_steps SET status=CASE status WHEN 'sending' THEN 'unknown' ELSE 'not_sent' END "
                        "WHERE user_id=? AND pet_id=? AND request_id=? AND status IN ('sending','pending')", key)
                    self._rollup(connection, key, row["kind"], failed=True)
        except Exception:
            self.close()
            raise ValueError("A separate private preferences intent ledger is required") from None

    def _connect(self):
        if self._closed:
            raise PreferencesError()
        connection = sqlite3.connect(self.path, timeout=1.0)
        connection.row_factory = sqlite3.Row
        return connection

    def _find(self, key):
        with closing(self._connect()) as connection:
            return connection.execute("SELECT * FROM pf_requests WHERE user_id=? AND pet_id=? AND request_id=?", key).fetchone()

    def _review(self, kind):
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT value FROM pf_meta WHERE key=?", ("review_" + kind,)).fetchone()
            _require(row is not None and row[0] in {"0", "1"})
            if row[0] == "1":
                return True
            rows = connection.execute("SELECT user_id,pet_id,request_id,status FROM pf_requests WHERE kind=? AND status IN ('unknown','sending')", (kind,)).fetchall()
        return any(row["status"] == "unknown" or
            (row["user_id"], row["pet_id"], row["request_id"]) not in self._tasks or
            self._tasks[(row["user_id"], row["pet_id"], row["request_id"])].done() for row in rows)

    def _receipt(self, key):
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT * FROM pf_requests WHERE user_id=? AND pet_id=? AND request_id=?", key).fetchone()
            if row is None:
                raise PreferencesError(404, "PREFERENCES_REQUEST_NOT_FOUND", "尚未找到原请求回执，请勿自动重发。")
            steps = connection.execute("SELECT step,status FROM pf_steps WHERE user_id=? AND pet_id=? AND request_id=? ORDER BY ordinal", key).fetchall()
        return validate_preferences(Receipt, {"requestId": row["request_id"], "kind": row["kind"], "status": row["status"],
            "steps": [dict(step) for step in steps], "updatedAt": row["updated_at"]})

    async def receipt(self, *, user_id, pet_id, request_id):
        _require(isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", request_id))
        return self._receipt((user_id, pet_id, request_id))

    async def _read_response(self, response):
        _require(response.status_code == 200 and response.headers.get("content-encoding", "").lower().strip() in {"", "identity"})
        length = response.headers.get("content-length")
        if length is not None:
            _require(re.fullmatch(r"[0-9]{1,10}", length) and int(length) <= _MAX_BYTES)
        raw = bytearray()
        async for chunk in response.aiter_bytes():
            _require(len(raw) + len(chunk) <= _MAX_BYTES)
            raw.extend(chunk)
        def unique(pairs):
            result = {}
            for key, value in pairs:
                _require(key not in result)
                result[key] = value
            return result
        def reject(_):
            raise ValueError("Non-JSON constant")
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=reject)
        _require(type(value) is dict and value.get("success") is True and type(value.get("data")) in (dict, list))
        return value["data"]

    def _headers(self, path):
        result = {"Accept": "application/json", "Accept-Encoding": "identity"}
        if path != "/health":
            result["X-API-Key"] = self._key
        if path.startswith("/pets"):
            result[self._authorization_header] = self._session
        elif self._authorization_header == 'X-Local-Gateway' and path != '/health':
            result[self._authorization_header] = self._session
        return result

    async def _get(self, path):
        _require(path in {"/health", "/device/status", "/pets", "/dialog/config"})
        endpoint = path if path == "/health" else "/api/v1" + path
        async with httpx.AsyncClient(transport=self._transport, trust_env=False, follow_redirects=False,
                                     timeout=httpx.Timeout(_READ_TIMEOUT)) as client:
            async with client.stream("GET", self._url + endpoint, headers=self._headers(path)) as response:
                return await self._read_response(response)

    async def _identity(self):
        health, device, pets = await asyncio.gather(self._get("/health"), self._get("/device/status"), self._get("/pets"))
        _require(type(health) is dict and health.get("status") == "ok" and health.get("adapter") == "local_k1")
        _require(type(device) is dict and device.get("adapter_mode") == "local_k1" and device.get("connected") is True)
        _require(isinstance(device.get("device_serial"), str) and secrets.compare_digest(device["device_serial"], self.expected_serial))
        _require(type(pets) is list and 0 < len(pets) <= 128 and all(type(pet) is dict for pet in pets))
        matches = [pet for pet in pets if pet.get("pet_id") == self._pet]
        _require(len(matches) == 1)
        pet = matches[0]
        _require(isinstance(pet.get("owner_user_id"), str) and _ID.fullmatch(pet["owner_user_id"]) and pet.get("online") is True)
        _require(isinstance(pet.get("serial_number"), str) and secrets.compare_digest(pet["serial_number"], self.expected_serial))
        return pet["owner_user_id"], pet, device

    @staticmethod
    def _config(data):
        _require(type(data) is dict and set(data) == {"follow_up_seconds", "minimum_seconds", "maximum_seconds", "source"})
        _require(type(data["follow_up_seconds"]) is int and 5 <= data["follow_up_seconds"] <= 120
            and type(data["minimum_seconds"]) is int and data["minimum_seconds"] == 5
            and type(data["maximum_seconds"]) is int and data["maximum_seconds"] == 120
            and data["source"] in {"default", "persisted"})
        return data

    async def _probe(self):
        _require(not self._closed)
        initial, config = await asyncio.gather(self._identity(), self._get("/dialog/config"))
        owner, pet, device = await self._identity()
        _require(secrets.compare_digest(initial[0], owner))
        self._config(config)
        name, stored = pet.get("name"), pet.get("volume")
        _require(isinstance(name, str) and 1 <= len(name) <= 20 and visible(name)
            and type(stored) is int and 0 <= stored <= 100)
        available = device.get("output_volume_available")
        _require(available is None or type(available) is bool)
        volume, muted = None, None
        if available is True:
            volume, muted = device.get("output_volume_percent"), device.get("output_muted")
            _require(volume is None or type(volume) is int and 0 <= volume <= 150)
            _require(muted is None or type(muted) is bool)
        _require(not self._closed)
        with closing(self._connect()) as connection, connection:
            fingerprint = hashlib.sha256(owner.encode()).hexdigest()
            prior = connection.execute("SELECT value FROM pf_meta WHERE key='owner'").fetchone()
            _require(prior is None or secrets.compare_digest(prior[0], fingerprint))
            connection.execute("INSERT OR IGNORE INTO pf_meta VALUES('owner',?)", (fingerprint,))
        return {"owner": owner, "name": name, "storedVolume": stored, "outputVolume": volume,
            "outputMuted": muted, "outputVolumeAvailable": available, "followUpSeconds": config["follow_up_seconds"],
            "followUpSource": config["source"], "minimumSeconds": 5, "maximumSeconds": 120}

    def _enabled(self, kind):
        return self.settings_enabled if kind == "settings" else self.feedback_enabled

    async def snapshot(self, *, user_id, pet_id):
        if self._closed:
            raise PreferencesError()
        probe, verified = {}, False
        try:
            probe = await asyncio.wait_for(self._probe(), _READ_TIMEOUT)
            verified = True
        except Exception:
            pass
        _require(not self._closed)
        reviews = {kind: self._review(kind) for kind in ("settings", "feedback")}
        settings = self.settings_enabled and verified and not reviews["settings"] and "settings" not in self._lanes
        feedback = self.feedback_enabled and verified and not reviews["feedback"] and "feedback" not in self._lanes
        reason = ("网页授权或设备连接尚未验证" if not verified else
            "有请求结果未确认，仅暂停对应类型写入；请查询原回执" if any(reviews.values()) else
            "有同类请求正在处理，请先查询回执" if self._lanes else
            "普通设置和反馈尚未开放" if not self.settings_enabled and not self.feedback_enabled else "")
        fields = ("name", "storedVolume", "outputVolume", "outputMuted", "outputVolumeAvailable",
                  "followUpSeconds", "followUpSource", "minimumSeconds", "maximumSeconds")
        return validate_preferences(Snapshot, {"verified": verified, "settingsEnabled": self.settings_enabled,
            "feedbackEnabled": self.feedback_enabled, "canChangeName": settings,
            "canChangeVolume": settings and probe.get("outputVolumeAvailable") is True,
            "canChangeFollowUp": settings, "canSubmitFeedback": feedback,
            **{key: probe.get(key) for key in fields}, "settingsReviewRequired": reviews["settings"],
            "feedbackReviewRequired": reviews["feedback"], "manualReviewRequired": any(reviews.values()),
            "reason": reason, "sampledAt": _utc()})

    async def submit(self, *, user_id, pet_id, request_id, kind, changes=None, category=None, content=None, authorize):
        _require(isinstance(user_id, str) and _ID.fullmatch(user_id) and isinstance(pet_id, str) and _ID.fullmatch(pet_id))
        body = {"requestId": request_id, "kind": kind}
        body.update({"changes": changes} if kind == "settings" else {"category": category, "content": content})
        command = Command.model_validate(body)
        payload = command.model_dump(exclude_unset=True)
        digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        key = user_id, pet_id, request_id
        prior = self._find(key)
        if prior is not None:
            if prior["kind"] != kind or not secrets.compare_digest(prior["digest"], digest):
                raise PreferencesError(409, "PREFERENCES_CONFLICT", "同一请求编号不能对应不同设置或反馈。")
            return self._receipt(key)
        if key in self._tasks:
            if self._signatures[key] != (kind, digest):
                raise PreferencesError(409, "PREFERENCES_CONFLICT", "同一请求编号不能对应不同设置或反馈。")
            return await asyncio.shield(self._tasks[key])
        if not self._enabled(kind):
            raise PreferencesError(403, "PREFERENCES_DISABLED", "该类型写入尚未开放。")
        if kind in self._lanes:
            raise PreferencesError(409, "PREFERENCES_BUSY", "同类请求正在处理，请查询原回执。")
        if self._review(kind):
            raise PreferencesError(409, "PREFERENCES_REVIEW_REQUIRED", "该类型已有结果未确认，请勿重新提交。")
        task = asyncio.create_task(self._submit(key, command, digest, authorize))
        self._tasks[key], self._signatures[key], self._lanes[kind] = task, (kind, digest), key
        def done(future):
            self._tasks.pop(key, None)
            self._signatures.pop(key, None)
            if self._lanes.get(kind) == key:
                self._lanes.pop(kind, None)
            if not future.cancelled():
                future.exception()
        task.add_done_callback(done)
        return await asyncio.shield(task)

    @staticmethod
    def _step_names(command):
        if command.kind == "feedback":
            return ["feedback"]
        return (["pet_settings"] if command.changes.name is not None or command.changes.volume is not None else []) + (
            ["follow_up"] if command.changes.followUpSeconds is not None else [])

    def _rollup(self, connection, key, kind, *, failed=False):
        states = [row[0] for row in connection.execute("SELECT status FROM pf_steps WHERE user_id=? AND pet_id=? AND request_id=? ORDER BY ordinal", key)]
        status = ("unknown" if "unknown" in states else "sending" if any(state in {"pending", "sending"} for state in states)
            else "partially_applied" if failed and "applied" in states else "recorded" if "applied" in states else "not_sent")
        connection.execute("UPDATE pf_requests SET status=?,updated_at=? WHERE user_id=? AND pet_id=? AND request_id=?", (status, _utc(), *key))
        if status == "unknown":
            connection.execute("UPDATE pf_meta SET value='1' WHERE key=?", ("review_" + kind,))

    def _step(self, key, kind, step, state, *, failed=False):
        with closing(self._connect()) as connection, connection:
            connection.execute("UPDATE pf_steps SET status=? WHERE user_id=? AND pet_id=? AND request_id=? AND step=?", (state, *key, step))
            if failed:
                connection.execute("UPDATE pf_steps SET status='not_sent' WHERE user_id=? AND pet_id=? AND request_id=? AND status='pending'", key)
            self._rollup(connection, key, kind, failed=failed)

    @staticmethod
    def _body(command, step, probe):
        if step == "feedback":
            return {"category": command.category, "content": command.content}
        if step == "follow_up":
            seconds = command.changes.followUpSeconds
            return {} if seconds == probe["followUpSeconds"] else {"follow_up_seconds": seconds}
        body = {}
        if command.changes.name is not None and command.changes.name != probe["name"]:
            body["name"] = command.changes.name
        target = command.changes.volume
        if target is not None and not (target == probe["storedVolume"] == probe["outputVolume"] and probe["outputMuted"] is (target == 0)):
            body["volume"] = target
        return body

    async def _check(self, command, authorize, deadline):
        async def check():
            await authorize()
            try:
                probe = await self._probe()
            except Exception:
                raise PreferencesError() from None
            await authorize()
            _require(not self._closed and time.monotonic() < deadline)
            if not self._enabled(command.kind):
                raise PreferencesError(403, "PREFERENCES_DISABLED", "该类型写入尚未开放。")
            if self._review(command.kind):
                raise PreferencesError(409, "PREFERENCES_REVIEW_REQUIRED", "该类型已有结果未确认，请勿重新提交。")
            if command.kind == "settings" and command.changes.volume is not None and probe["outputVolumeAvailable"] is not True:
                raise PreferencesError(409, "PREFERENCES_VOLUME_UNAVAILABLE", "当前实际输出音量接口不可用，未修改设置。")
            return probe
        try:
            return await asyncio.wait_for(check(), max(0.001, deadline - time.monotonic()))
        except TimeoutError:
            raise PreferencesError() from None

    async def _submit(self, key, command, digest, authorize):
        deadline = time.monotonic() + _PREFLIGHT_TIMEOUT
        await self._check(command, authorize, deadline)
        steps = self._step_names(command)
        with closing(self._connect()) as connection, connection:
            connection.execute("INSERT INTO pf_requests VALUES(?,?,?,?,?,'sending',?)", (*key, command.kind, digest, _utc()))
            connection.executemany("INSERT INTO pf_steps VALUES(?,?,?,?,?,'pending')", [(*key, step, index) for index, step in enumerate(steps)])
        for step in steps:
            dispatched = [False]
            try:
                # An earlier valid acknowledgement may take several seconds.
                # Its wait does not consume the next step's verification
                # budget; each step still keeps one fixed dispatch deadline.
                step_deadline = time.monotonic() + _PREFLIGHT_TIMEOUT
                await self._dispatch(key, command, step, authorize, step_deadline, dispatched)
            except BaseException as error:
                self._step(key, command.kind, step, "unknown" if dispatched[0] else "not_sent", failed=True)
                if not isinstance(error, Exception):
                    raise
                break
        return self._receipt(key)

    async def _dispatch(self, key, command, step, authorize, deadline, dispatched):
        async def send():
            async with httpx.AsyncClient(transport=self._transport, trust_env=False, follow_redirects=False,
                                         timeout=httpx.Timeout(_ACK_TIMEOUT)) as client:
                # Client entry can yield; all checks are adjacent to each patch,
                # not just before the first one. The original deadline remains.
                probe = await self._check(command, authorize, deadline)
                body = self._body(command, step, probe)
                if not body:
                    self._step(key, command.kind, step, "not_sent")
                    return
                path = "/dialog/config" if step == "follow_up" else "/pets/" + self._pet + ("/feedback" if step == "feedback" else "/settings")
                self._step(key, command.kind, step, "sending")
                _require(not self._closed and self._enabled(command.kind) and time.monotonic() < deadline)
                dispatched[0] = True
                async with client.stream("POST" if step == "feedback" else "PATCH", self._url + "/api/v1" + path,
                                         headers=self._headers(path), json=body) as response:
                    data = await self._read_response(response)
                self._ack(step, body, data, probe["owner"])
                self._step(key, command.kind, step, "applied")
        await asyncio.wait_for(send(), _ACK_TIMEOUT)

    def _ack(self, step, body, data, owner):
        _require(type(data) is dict)
        if step == "pet_settings":
            _require(data.get("pet_id") == self._pet and data.get("owner_user_id") == owner and data.get("serial_number") == self.expected_serial)
            for field, value in body.items():
                _require(type(data.get(field)) is type(value) and data[field] == value)
        elif step == "follow_up":
            self._config(data)
            _require(data["source"] == "persisted" and data["follow_up_seconds"] == body["follow_up_seconds"])
        else:
            _require(set(data) == {"feedback_id", "category", "created_at"} and isinstance(data["feedback_id"], str)
                and re.fullmatch(r"fb_[A-Za-z0-9_-]{1,100}", data["feedback_id"]) and data["category"] == body["category"])
            _require(isinstance(data["created_at"], str) and len(data["created_at"]) <= 40)
            timestamp = datetime.fromisoformat(data["created_at"].replace("Z", "+00:00"))
            _require(timestamp.tzinfo is not None and timestamp.utcoffset() == timezone.utc.utcoffset(timestamp))
