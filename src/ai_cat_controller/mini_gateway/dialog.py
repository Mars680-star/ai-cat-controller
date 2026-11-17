"""GET-only, explicit persisted-dialog capability. No ledger or disk writes."""

import asyncio
from collections import OrderedDict
from datetime import datetime, timezone
import hashlib
import hmac
import json
import re
import secrets
import time
from urllib.parse import quote

import httpx
from ai_cat_controller.local_gateway.delegation import bridge_authorization

from .motion import _private_url
from .dialog_models import Detail, ListView, Message, Status, validate_view

_CAPABILITY = "persisted_dialog_readonly_v1"
_TIMEOUT = 4.0
_MAX_BYTES = 4 * 1024 * 1024
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


class DialogReadError(RuntimeError):
    def __init__(self, status=503, code="DIALOG_READ_UNAVAILABLE", message="只读对话暂不可用，请检查网页授权及只读接口版本。"):
        super().__init__(message)
        self.status_code, self.code = status, code


def _require(value):
    if not value:
        raise ValueError("Unverified read-only dialog")


def _utc(value=None):
    if value is None:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    _require(isinstance(value, str) and len(value) <= 40)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    _require(parsed.tzinfo is not None)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class LocalDialogReader:
    def __init__(self, *, upstream_url, api_key, expected_serial, product_session=None, product_pet_id, transport=None, local_delegation=None):
        self._url = _private_url(upstream_url)
        _require(isinstance(api_key, str) and 1 <= len(api_key) <= 4096 and all(33 <= ord(c) <= 126 for c in api_key))
        _require(isinstance(expected_serial, str) and re.fullmatch(r"[A-Za-z0-9_-]{6,64}", expected_serial))
        self._authorization_header, credential = bridge_authorization(upstream_url,product_session,local_delegation)
        _require(isinstance(product_pet_id, str) and _ID.fullmatch(product_pet_id))
        self.expected_serial = expected_serial
        self._key, self._session, self._pet = api_key, credential, product_pet_id
        self._transport, self._owner = transport, None
        self._secret = secrets.token_bytes(32)
        self._mapping = OrderedDict()
        self._closed = False

    def close(self):
        self._closed = True
        self._mapping.clear()

    async def _get(self, path, *, expect_list=False):
        owned = "/pets/" + self._pet + "/dialog-readonly/"
        _require(path in {"/health", "/device/status", "/pets", owned + "status", owned + "conversations"}
                 or (path.startswith(owned + "conversations/") and len(path) <= len(owned) + 800))
        headers = {"Accept": "application/json", "Accept-Encoding": "identity"}
        if path != "/health":
            headers["X-API-Key"] = self._key
        if path.startswith("/pets"):
            headers[self._authorization_header] = self._session
        elif self._authorization_header == 'X-Local-Gateway' and path != '/health':
            headers[self._authorization_header] = self._session
        endpoint = path if path == "/health" else "/api/v1" + path
        async with httpx.AsyncClient(transport=self._transport, trust_env=False, follow_redirects=False,
                                     timeout=httpx.Timeout(_TIMEOUT)) as client:
            async with client.stream("GET", self._url + endpoint, headers=headers) as response:
                _require(response.status_code == 200)
                _require(response.headers.get("content-encoding", "").lower().strip() in {"", "identity"})
                length = response.headers.get("content-length")
                if length is not None:
                    _require(re.fullmatch(r"[0-9]{1,10}", length) and int(length) <= _MAX_BYTES)
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    _require(len(raw) + len(chunk) <= _MAX_BYTES)
                    raw.extend(chunk)
        def reject(_):
            raise ValueError("Non-JSON constant")
        payload = json.loads(raw.decode("utf-8"), parse_constant=reject)
        _require(type(payload) is dict and payload.get("success") is True
                 and type(payload.get("data")) is (list if expect_list else dict))
        return payload["data"]

    async def _verify(self):
        health, device, pets = await asyncio.gather(self._get("/health"), self._get("/device/status"), self._get("/pets", expect_list=True))
        _require(health.get("status") == "ok" and health.get("adapter") == "local_k1")
        _require(device.get("adapter_mode") == "local_k1" and device.get("connected") is True)
        serial = device.get("device_serial")
        _require(isinstance(serial, str) and secrets.compare_digest(serial, self.expected_serial))
        _require(0 < len(pets) <= 128 and all(type(pet) is dict for pet in pets))
        matches = [pet for pet in pets if pet.get("pet_id") == self._pet]
        _require(len(matches) == 1)
        owner, serial = matches[0].get("owner_user_id"), matches[0].get("serial_number")
        _require(isinstance(owner, str) and _ID.fullmatch(owner) and matches[0].get("online") is True)
        _require(isinstance(serial, str) and secrets.compare_digest(serial, self.expected_serial))
        _require(self._owner is None or secrets.compare_digest(owner, self._owner))
        _require(not self._closed)
        return owner

    def _opaque(self, prefix, scope, raw):
        digest = hmac.new(self._secret, json.dumps([scope, raw], separators=(",", ":")).encode(), hashlib.sha256).hexdigest()
        return prefix + digest[:48]

    def _summary(self, item, scope):
        raw_id = item["conversation_id"]
        _require(isinstance(raw_id, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{4,256}", raw_id))
        opaque = self._opaque("conv_", scope, raw_id)
        self._mapping[(scope, opaque)] = (raw_id, time.monotonic())
        self._mapping.move_to_end((scope, opaque))
        while len(self._mapping) > 256:
            self._mapping.popitem(last=False)
        preview, assistant = item["preview"], item["assistant_preview"]
        _require(isinstance(preview, str) and (assistant is None or isinstance(assistant, str)))
        return {"conversationId": opaque, "status": item["status"], "source": item["source"],
                "startedAt": _utc(item["started_at"]), "updatedAt": _utc(item["updated_at"]),
                "durationSeconds": item["duration_seconds"], "messageCount": item["message_count"],
                "userMessageCount": item["user_message_count"], "assistantMessageCount": item["assistant_message_count"],
                "preview": preview[:160], "assistantPreview": assistant[:160] if assistant is not None else None}

    def _revision(self, scope, content):
        return hmac.new(self._secret, json.dumps([scope, content], sort_keys=True, ensure_ascii=False,
                        separators=(",", ":")).encode(), hashlib.sha256).hexdigest()

    async def read(self, *, user_id, pet_id, operation, conversation_id=None, revision=None):
        if self._closed:
            raise DialogReadError()
        _require(isinstance(user_id, str) and _ID.fullmatch(user_id) and isinstance(pet_id, str) and _ID.fullmatch(pet_id))
        _require(operation in {"status", "list", "detail"})
        _require(revision is None or (isinstance(revision, str) and re.fullmatch(r"[0-9a-f]{64}", revision)))
        try:
            return await asyncio.wait_for(self._read(user_id, pet_id, operation, conversation_id, revision), _TIMEOUT)
        except DialogReadError:
            raise
        except Exception:
            raise DialogReadError() from None

    async def _read(self, user, pet, operation, conversation_id, revision):
        owner = await self._verify()
        scope = (user, pet, owner)
        base = "/pets/" + self._pet + "/dialog-readonly/"
        raw_id = None
        if operation == "detail":
            entry = self._mapping.get((scope, conversation_id))
            if entry is None or time.monotonic() - entry[1] > 600:
                raise DialogReadError(404, "DIALOG_CONVERSATION_EXPIRED", "会话入口已过期，请返回并刷新列表。")
            raw_id = entry[0]
            path = base + "conversations/" + quote(raw_id, safe="")
        else:
            path = base + ("status" if operation == "status" else "conversations")
        raw = await self._get(path)
        _require(raw.get("capability") == _CAPABILITY and raw.get("read_only") is True)
        if operation == "status":
            data = raw["status"]
            def milliseconds(value):
                _require(type(value) is int and 0 <= value <= 8_640_000_000_000)
                return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat().replace("+00:00", "Z") if value else None
            content = {"state": data["state"], "sessionActive": data["session_active"],
                       "audioPlaying": data.get("audio_playing"), "stale": data["stale"],
                       "updatedAt": milliseconds(data["updated_at_ms"]), "sequence": data["sequence"],
                       "followUpDeadlineAt": milliseconds(data["follow_up_deadline_ms"]), "readOnly": True}
            content["revision"] = self._revision(scope, content)
            content["sampledAt"] = _utc()
            result = validate_view(Status, content)
        elif operation == "list":
            _require(raw.get("scope") == "persisted_only" and raw.get("limit") == 30
                     and type(raw.get("conversations")) is list and len(raw["conversations"]) <= 30)
            items = [self._summary(item, scope) for item in raw["conversations"]]
            token = self._revision(scope, items)
            result = validate_view(ListView, {"items": [] if token == revision else items, "revision": token,
                "limit": 30, "scope": "persisted_only", "unchanged": token == revision, "sampledAt": _utc()})
        else:
            _require(raw.get("scope") == "persisted_only" and raw.get("limit") == 100
                     and type(raw.get("messages")) is list and len(raw["messages"]) <= 100)
            _require(raw["conversation"]["conversation_id"] == raw_id)
            summary = self._summary(raw["conversation"], scope)
            messages = []
            for message in raw["messages"]:
                _require(message.get("conversation_id") == raw_id and isinstance(message.get("message_id"), str)
                         and 1 <= len(message["message_id"]) <= 256)
                messages.append(validate_view(Message, {"messageId": self._opaque("msg_", scope, [raw_id, message["message_id"]]),
                                 "role": message["role"], "content": message["content"], "createdAt": _utc(message["created_at"])}))
            _require(type(raw.get("truncated")) is bool and raw["truncated"] == (summary["messageCount"] > len(messages))
                     and len(messages) == min(100, summary["messageCount"]))
            window, content_bytes = [], 0
            for message in reversed(messages):
                size = len(message["content"].encode("utf-8"))
                if content_bytes + size > 192 * 1024:
                    break
                window.append(message)
                content_bytes += size
            messages = list(reversed(window))
            truncated = summary["messageCount"] > len(messages)
            token = self._revision(scope, [summary, messages, truncated])
            result = validate_view(Detail, {"conversation": summary, "messages": [] if token == revision else messages,
                "revision": token, "limit": 100, "scope": "persisted_only", "truncated": truncated,
                "windowMessageCount": len(messages),
                "unchanged": token == revision, "sampledAt": _utc()})
        _require(secrets.compare_digest(owner, await self._verify()))
        # Publish the initial process-local owner pin only after the capability,
        # complete public schema and final identity check have all succeeded.
        # No await between the compare and assignment: competing first reads
        # cannot publish responses for two different owners.
        _require(not self._closed and (self._owner is None or secrets.compare_digest(owner, self._owner)))
        self._owner = owner
        return result
