"""Opt-in dialog commands with a private, durable at-most-once intent ledger.

The companion reader stays GET-only. A command acknowledgement proves receipt,
never playback or an answer. Unknown delivery permanently blocks new text/listen;
an independently authorized interrupt may be sent but never clears that latch.
"""

import asyncio
from contextlib import closing
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
from ai_cat_controller.local_gateway.delegation import bridge_authorization

from .dialog import LocalDialogReader
from .dialog_models import Status as DialogStatus
from .dialog_control_models import Command, Control, Receipt, validate_control
from .motion import _private_url, _utc

_PREFLIGHT_TIMEOUT = 3.0
_ACK_TIMEOUT = 35.0
_MAX_ACK_BYTES = 16 * 1024
_LISTEN_STATES = {"ready", "waiting_wake", "interrupted", "offline", "echo_guard"}
_TEXT_STATES = {"ready", "followup_listening", "interrupted", "offline"}
_STATES = set(DialogStatus.model_fields["state"].annotation.__args__)
_PATHS = {"listen": "/dialog/wake", "interrupt": "/dialog/interrupt", "text": "/dialog/text"}


class DialogControlError(RuntimeError):
    def __init__(self, status=503, code="DIALOG_CONTROL_UNAVAILABLE", message="对话控制暂不可用，请检查授权与连接。"):
        super().__init__(message)
        self.status_code, self.code = status, code


def _require(condition):
    if not condition:
        raise ValueError("Unverified dialog control")


class LocalDialogController:
    def __init__(self, *, db_path, enabled=False, upstream_url, api_key, expected_serial,
                 product_session=None, product_pet_id, reader=None, transport=None, local_delegation=None):
        if type(enabled) is not bool:
            raise ValueError("Dialog control enablement must be explicit")
        self.enabled, self.expected_serial = enabled, expected_serial
        self._url = _private_url(upstream_url)
        self._key, self._product_pet = api_key, product_pet_id
        self._authorization_header, credential = bridge_authorization(upstream_url,product_session,local_delegation)
        self._local_delegation = local_delegation
        self._reader = reader or LocalDialogReader(upstream_url=upstream_url, api_key=api_key,
            expected_serial=expected_serial, product_session=product_session, product_pet_id=product_pet_id,
            transport=transport, local_delegation=local_delegation)
        _require(self._reader.expected_serial == expected_serial and self._reader._url == self._url
                 and self._reader._pet == product_pet_id and self._reader._key == api_key
                 and self._reader._session == credential and self._reader._authorization_header == self._authorization_header)
        self._transport = transport
        self._tasks, self._signatures, self._lanes = {}, {}, {}
        self._lock_descriptor, self._closed = None, False
        self.path = Path(db_path).absolute()
        self._prepare_store()

    def close(self):
        if any(not task.done() for task in self._tasks.values()):
            raise RuntimeError("Dialog submissions must settle before closing")
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
            for ancestor in self.path.parent.parents:
                info = ancestor.stat()
                _require(info.st_uid in (0, os.getuid()) and
                    (not info.st_mode & 0o022 or (info.st_uid == 0 and info.st_mode & stat.S_ISVTX)))
            descriptor = os.open(str(self.path) + ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            self._lock_descriptor = descriptor
            info = os.fstat(descriptor)
            _require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1 and not info.st_mode & 0o077)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            created = not self.path.exists()
            descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                info = os.fstat(descriptor)
                _require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1 and not info.st_mode & 0o077)
            finally:
                os.close(descriptor)
            _require(not any(Path(str(self.path) + suffix).exists() for suffix in ("-wal", "-shm")))
            pin = hashlib.sha256(json.dumps([self._url, self.expected_serial, self._product_pet], separators=(",", ":")).encode()).hexdigest()
            if not created:
                uri = "file:" + quote(str(self.path), safe="/") + "?mode=ro&immutable=1"
                with closing(sqlite3.connect(uri, uri=True)) as connection:
                    _require({row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")} == {"dc_meta", "dc_requests"})
                    _require(connection.execute("SELECT value FROM dc_meta WHERE key='schema'").fetchone() == ("dialog_control_v1",))
                    _require(connection.execute("SELECT value FROM dc_meta WHERE key='pin'").fetchone() == (pin,))
            with closing(self._connect()) as connection, connection:
                if created:
                    connection.executescript("""
                        CREATE TABLE dc_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                        CREATE TABLE dc_requests(user_id TEXT NOT NULL,pet_id TEXT NOT NULL,
                            request_id TEXT NOT NULL,kind TEXT NOT NULL,digest TEXT NOT NULL,
                            status TEXT NOT NULL,updated_at TEXT NOT NULL,
                            PRIMARY KEY(user_id,pet_id,request_id));
                    """)
                    connection.executemany("INSERT INTO dc_meta VALUES(?,?)", [("schema", "dialog_control_v1"), ("pin", pin), ("review", "0")])
                changed = connection.execute("UPDATE dc_requests SET status='unknown',updated_at=? WHERE status='sending'", (_utc(),)).rowcount
                if changed:
                    connection.execute("UPDATE dc_meta SET value='1' WHERE key='review'")
        except Exception:
            self.close()
            raise ValueError("A separate private dialog intent ledger is required") from None

    def _connect(self):
        if self._closed:
            raise DialogControlError()
        connection = sqlite3.connect(self.path, timeout=1.0)
        connection.row_factory = sqlite3.Row
        return connection

    def _find(self, user, pet, request):
        with closing(self._connect()) as connection:
            return connection.execute("SELECT * FROM dc_requests WHERE user_id=? AND pet_id=? AND request_id=?", (user, pet, request)).fetchone()

    def _review(self):
        with closing(self._connect()) as connection:
            value = connection.execute("SELECT value FROM dc_meta WHERE key='review'").fetchone()
            _require(value is not None and value[0] in {"0", "1"})
            if value[0] == "1":
                return True
            rows = connection.execute("SELECT user_id,pet_id,request_id,status FROM dc_requests WHERE status IN ('unknown','sending')").fetchall()
        # A post-delivery SQLite failure can leave a durable sending row after
        # its task exits. Treat such an orphan as unknown immediately, not only
        # after restart; never let a failed receipt update reopen normal sends.
        return any(row["status"] == "unknown" or
            (row["user_id"], row["pet_id"], row["request_id"]) not in self._tasks or
            self._tasks[(row["user_id"], row["pet_id"], row["request_id"])].done()
            for row in rows)

    @staticmethod
    def _receipt(row):
        return validate_control(Receipt, {"requestId": row["request_id"], "kind": row["kind"],
            "status": row["status"], "updatedAt": row["updated_at"]})

    async def receipt(self, *, user_id, pet_id, request_id):
        Command.model_validate({"requestId": request_id, "kind": "listen"})
        row = self._find(user_id, pet_id, request_id)
        if row is None:
            raise DialogControlError(404, "DIALOG_REQUEST_NOT_FOUND", "尚未找到该请求回执，请勿自动重发。")
        return self._receipt(row)

    async def _probe(self):
        _require(not self._closed)
        owner = await self._reader._verify()
        raw = await self._reader._get("/pets/" + self._product_pet + "/dialog-readonly/status")
        _require(raw.get("capability") == "persisted_dialog_readonly_v1" and raw.get("read_only") is True
                 and type(raw.get("status")) is dict)
        data = raw["status"]
        state = data.get("state")
        state = state if isinstance(state, str) and state in _STATES else "unavailable"
        stale = data.get("stale") if type(data.get("stale")) is bool else True
        active = data.get("session_active") if type(data.get("session_active")) is bool else True
        if (type(data.get("updated_at_ms")) is not int or not 0 <= data["updated_at_ms"] <= 9_007_199_254_740_991
                or type(data.get("sequence")) is not int or not 0 <= data["sequence"] <= 9_007_199_254_740_991):
            stale = True
        _require(secrets.compare_digest(owner, await self._reader._verify()))
        _require(not self._closed)
        with closing(self._connect()) as connection, connection:
            fingerprint = hashlib.sha256(owner.encode()).hexdigest()
            prior = connection.execute("SELECT value FROM dc_meta WHERE key='owner'").fetchone()
            _require(prior is None or secrets.compare_digest(prior[0], fingerprint))
            connection.execute("INSERT OR IGNORE INTO dc_meta VALUES('owner',?)", (fingerprint,))
        return {"owner": owner, "state": state, "stale": stale or state == "unavailable", "active": active}

    def _available(self, probe, kind):
        if kind == "interrupt":
            return True
        if probe["stale"] or self._review():
            return False
        return ((probe["state"] in _LISTEN_STATES and not probe["active"]) if kind == "listen"
                else probe["state"] in _TEXT_STATES)

    async def snapshot(self, *, user_id, pet_id):
        if self._closed:
            raise DialogControlError()
        verified, probe = False, {"state": "unavailable", "stale": True, "active": True}
        try:
            probe = await asyncio.wait_for(self._probe(), _PREFLIGHT_TIMEOUT)
            verified = True
        except Exception:
            pass
        review = self._review()
        normal_busy, interrupt_busy = (lane in self._lanes for lane in ("normal", "interrupt"))
        listen = self.enabled and verified and not normal_busy and not interrupt_busy and self._available(probe, "listen")
        text = self.enabled and verified and not normal_busy and not interrupt_busy and self._available(probe, "text")
        interrupt = self.enabled and verified and not interrupt_busy
        reason = ("对话控制尚未开放" if not self.enabled else
            "网页授权或设备只读接口尚未验证" if not verified else
            "有请求结果未确认，请勿重新发送；打断不会解除核验锁" if review else
            "语音状态已过期或不可用，仅可请求打断" if probe["stale"] else
            "对话请求正在处理，请查询原请求回执" if normal_busy or interrupt_busy else
            "当前语音阶段暂不接受开始聆听或文字问题" if not listen and not text else "")
        return validate_control(Control, {"enabled": self.enabled, "verified": verified, "canListen": listen,
            "canInterrupt": interrupt, "canSendText": text, "manualReviewRequired": review, "reason": reason,
            "state": probe["state"], "stale": probe["stale"], "sampledAt": _utc()})

    async def submit(self, *, user_id, pet_id, request_id, kind, content=None, authorize):
        _require(isinstance(user_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", user_id)
                 and isinstance(pet_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", pet_id))
        payload = {"requestId": request_id, "kind": kind}
        if content is not None or kind == "text":
            payload["content"] = content
        command = Command.model_validate(payload)
        digest = hashlib.sha256(json.dumps([kind, command.content], ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        key = user_id, pet_id, request_id
        previous = self._find(*key)
        if previous is not None:
            if previous["kind"] != kind or not secrets.compare_digest(previous["digest"], digest):
                raise DialogControlError(409, "DIALOG_COMMAND_CONFLICT", "同一请求编号不能对应不同命令或正文。")
            return self._receipt(previous)
        if key in self._tasks:
            if self._signatures[key] != (kind, digest):
                raise DialogControlError(409, "DIALOG_COMMAND_CONFLICT", "同一请求编号不能对应不同命令或正文。")
            return await asyncio.shield(self._tasks[key])
        if not self.enabled or self._closed:
            raise DialogControlError(403, "DIALOG_CONTROL_DISABLED", "对话控制尚未开放。")
        lane = "interrupt" if kind == "interrupt" else "normal"
        if lane in self._lanes or (lane == "normal" and "interrupt" in self._lanes):
            raise DialogControlError(409, "DIALOG_COMMAND_BUSY", "上一条同类请求仍在处理，请查询回执。")
        if kind != "interrupt" and self._review():
            raise DialogControlError(409, "DIALOG_REVIEW_REQUIRED", "已有请求结果未确认，不可重新发送；可独立请求打断。")
        task = asyncio.create_task(self._submit(user_id, pet_id, request_id, kind, command.content, digest, authorize))
        self._tasks[key], self._signatures[key], self._lanes[lane] = task, (kind, digest), key
        def done(future):
            self._tasks.pop(key, None)
            self._signatures.pop(key, None)
            if self._lanes.get(lane) == key:
                self._lanes.pop(lane, None)
            if not future.cancelled():
                future.exception()
        task.add_done_callback(done)
        return await asyncio.shield(task)

    async def _submit(self, user, pet, request, kind, content, digest, authorize):
        deadline = time.monotonic() + _PREFLIGHT_TIMEOUT
        async def check():
            await authorize()
            if kind != "interrupt" and "interrupt" in self._lanes:
                raise DialogControlError(409, "DIALOG_COMMAND_BUSY", "打断请求正在处理，请先查询其回执。")
            try:
                probe = await self._probe()
            except Exception:
                raise DialogControlError() from None
            if not self._available(probe, kind):
                raise DialogControlError(409, "DIALOG_COMMAND_NOT_READY", "当前状态不允许该命令，请刷新后查看。")
            await authorize()
        try:
            await asyncio.wait_for(check(), max(0.001, deadline - time.monotonic()))
        except TimeoutError:
            raise DialogControlError() from None
        with closing(self._connect()) as connection, connection:
            connection.execute("INSERT INTO dc_requests VALUES(?,?,?,?,?,'sending',?)", (user, pet, request, kind, digest, _utc()))
        dispatched = [False]
        try:
            await asyncio.wait_for(check(), max(0.001, deadline - time.monotonic()))
            await self._post(kind, content, self._upstream_request(user, pet, request), deadline, dispatched, authorize)
        except BaseException as error:
            state = "unknown" if dispatched[0] else "not_sent"
            self._update(user, pet, request, state)
            if not isinstance(error, Exception) or (not dispatched[0] and not isinstance(error, DialogControlError)):
                raise
        else:
            self._update(user, pet, request, "accepted")
        return self._receipt(self._find(user, pet, request))

    @staticmethod
    def _upstream_request(user, pet, request):
        return "dc_" + hashlib.sha256(json.dumps([user, pet, request], separators=(",", ":")).encode()).hexdigest()[:60]

    def _update(self, user, pet, request, state):
        with closing(self._connect()) as connection, connection:
            connection.execute("UPDATE dc_requests SET status=?,updated_at=? WHERE user_id=? AND pet_id=? AND request_id=?",
                               (state, _utc(), user, pet, request))
            if state == "unknown":
                connection.execute("UPDATE dc_meta SET value='1' WHERE key='review'")

    async def _post(self, kind, content, request_id, deadline, dispatched, authorize):
        body = {"request_id": request_id}
        if kind == "text":
            body["content"] = content
        async def send():
            async with httpx.AsyncClient(transport=self._transport, trust_env=False, follow_redirects=False,
                                         timeout=httpx.Timeout(_ACK_TIMEOUT)) as client:
                # Client entry may itself yield. Recheck the dedicated version
                # capability and voice eligibility as well as the physical /
                # webpage identity after that boundary, inside the original
                # pre-dispatch deadline. Interrupt ignores busy/stale voice,
                # never the capability or identity proof.
                try:
                    probe = await asyncio.wait_for(self._probe(), max(0.001, deadline - time.monotonic()))
                except Exception:
                    raise DialogControlError() from None
                if not self._available(probe, kind):
                    raise DialogControlError(409, "DIALOG_COMMAND_NOT_READY", "当前状态不允许该命令，请刷新后查看。")
                owner = probe["owner"]
                with closing(self._connect()) as connection:
                    pinned = connection.execute("SELECT value FROM dc_meta WHERE key='owner'").fetchone()
                    _require(pinned is not None and secrets.compare_digest(pinned[0], hashlib.sha256(owner.encode()).hexdigest()))
                await authorize()
                _require(not self._closed and time.monotonic() < deadline)
                if not self.enabled:
                    raise DialogControlError(403, "DIALOG_CONTROL_DISABLED", "对话控制尚未开放。")
                if kind != "interrupt" and "interrupt" in self._lanes:
                    raise DialogControlError(409, "DIALOG_COMMAND_BUSY", "打断请求正在处理，请先查询其回执。")
                if kind != "interrupt" and self._review():
                    raise DialogControlError(409, "DIALOG_REVIEW_REQUIRED", "已有请求结果未确认，不可重新发送。")
                dispatched[0] = True
                async with client.stream("POST", self._url + "/api/v1" + _PATHS[kind],
                    headers={"X-API-Key": self._key, "Accept": "application/json", "Accept-Encoding": "identity",
                        **({'X-Local-Gateway':self._local_delegation} if self._local_delegation is not None else {})}, json=body) as response:
                    _require(response.status_code == 200)
                    _require(response.headers.get("content-encoding", "").lower().strip() in {"", "identity"})
                    length = response.headers.get("content-length")
                    if length is not None:
                        _require(re.fullmatch(r"[0-9]{1,10}", length) and int(length) <= _MAX_ACK_BYTES)
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        _require(len(raw) + len(chunk) <= _MAX_ACK_BYTES)
                        raw.extend(chunk)
            payload = json.loads(raw.decode("utf-8"))
            _require(type(payload) is dict and payload.get("success") is True and type(payload.get("data")) is dict)
            data = payload["data"]
            _require(set(data) == {"dialog_state", "request_id", "changed"} and data["request_id"] == request_id
                     and type(data["changed"]) is bool)
            states = {"listen": {"awake", "waking"}, "interrupt": {"interrupted"}, "text": {"queued"}}
            _require(data["dialog_state"] in states[kind] and (kind == "listen" or data["changed"] is True))
        await asyncio.wait_for(send(), _ACK_TIMEOUT)
