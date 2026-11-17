"""Purely local dialog reads: dedicated upstream contract, no event importing."""

import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import get_args

import httpx
import pytest
from fastapi.testclient import TestClient

from ai_cat_controller.mini_gateway.app import create_app
from ai_cat_controller.mini_gateway.config import MiniGatewayConfig
from ai_cat_controller.mini_gateway.dialog import DialogReadError, LocalDialogReader
from ai_cat_controller.mini_gateway.dialog_models import State
from ai_cat_controller.mini_gateway.store import SessionStore
from ai_cat_controller.persistence.sqlite_repository import SQLiteRepository

SERIAL = "7c2b63fd4a128"
PET = "pet_upstream"
OWNER = "usr_product_owner"
CAPABILITY = "persisted_dialog_readonly_v1"
STAMP = "2026-10-21T02:00:00Z"


def summary():
    return {"conversation_id": "native-raw-conversation", "status": "complete", "source": "device",
            "started_at": STAMP, "updated_at": STAMP, "duration_seconds": 0, "message_count": 2,
            "user_message_count": 1, "assistant_message_count": 1, "preview": "你好", "assistant_preview": "你好呀"}


class Upstream:
    def __init__(self):
        self.requests, self.owner, self.serial = [], OWNER, SERIAL
        self.capability = CAPABILITY
        self.during = lambda: None
        self.summary = summary()
        self.status = {"state": "thinking", "session_active": True, "audio_playing": False,
                       "stale": False, "updated_at_ms": 1792548000000, "sequence": 10,
                       "follow_up_deadline_ms": 0, "message": "/root/private operational error",
                       "personality_revision": "private-revision", "source": "local_k1"}

    async def __call__(self, request):
        self.requests.append(request)
        assert request.method == "GET"
        path = request.url.path
        if path == "/health":
            data = {"status": "ok", "adapter": "local_k1"}
        elif path == "/api/v1/device/status":
            data = {"adapter_mode": "local_k1", "connected": True, "device_serial": self.serial}
        elif path == "/api/v1/pets":
            data = [{"pet_id": PET, "owner_user_id": self.owner, "serial_number": SERIAL, "online": True}]
        else:
            self.during()
            base = "/api/v1/pets/" + PET + "/dialog-readonly/"
            assert path.startswith(base), "Old importing API must never be used"
            data = {"capability": self.capability, "read_only": True, "scope": "persisted_only"}
            if path == base + "status":
                data["status"] = self.status
            elif path == base + "conversations":
                data.update(limit=30, conversations=[self.summary])
            elif path == base + "conversations/native-raw-conversation":
                data.update(limit=100, truncated=False, conversation=self.summary,
                    messages=[{"message_id": "raw-message-one", "conversation_id": "native-raw-conversation",
                               "role": "user", "content": "你好\n继续说", "created_at": STAMP, "voice_id": "private-voice"},
                              {"message_id": "raw-message-two", "conversation_id": "native-raw-conversation",
                               "role": "assistant", "content": "你好呀", "created_at": STAMP}])
            else:
                raise AssertionError("Unexpected detail URL")
        return httpx.Response(200, json={"success": True, "data": data})


def reader(device=None):
    return LocalDialogReader(upstream_url="http://192.168.1.152:8000", api_key="test-private-key",
        expected_serial=SERIAL, product_session="test_private_webpage_session", product_pet_id=PET,
        transport=httpx.MockTransport(device or Upstream()))


@pytest.mark.asyncio
async def test_status_is_filtered_and_never_exposes_control_or_device_metadata():
    device = Upstream()
    bridge = reader(device)
    result = await bridge.read(user_id="usr_mini", pet_id="pet_mini", operation="status")
    assert result["state"] == "thinking" and result["sequence"] == 10 and result["readOnly"] is True
    assert result["updatedAt"].endswith("Z") and result["followUpDeadlineAt"] is None
    rendered = json.dumps(result)
    assert all(value not in rendered for value in ["/root/", "private", PET, OWNER, SERIAL, "canInterrupt"])
    assert all(request.method == "GET" for request in device.requests)
    assert not hasattr(bridge, "path")


@pytest.mark.asyncio
async def test_missing_playback_state_is_unknown_and_zero_source_time_is_not_faked():
    device = Upstream()
    device.status.pop("audio_playing")
    device.status.update(state="offline", updated_at_ms=0, sequence=0, session_active=False)
    result = await reader(device).read(user_id="usr", pet_id="pet", operation="status")
    assert result["audioPlaying"] is None and result["updatedAt"] is None
    assert result["state"] == "offline" and result["sequence"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["processing_audio", "followup_preparing", "connected",
    "proactive_queued", "proactive_speaking", "proactive_finishing", "stopping"])
async def test_native_inflight_phases_are_preserved_without_rejecting_valid_status(phase):
    device = Upstream()
    device.status.update(state=phase, session_active=True)
    result = await reader(device).read(user_id="usr", pet_id="pet", operation="status")
    assert result["state"] == phase and result["sessionActive"] is True
    assert result["readOnly"] is True
    assert all(request.method == "GET" for request in device.requests)


def test_public_dialog_state_allowlist_covers_actual_native_status_literals():
    source = (Path(__file__).resolve().parents[2] / "native/dialog/volc_conv_ai_demo.c").read_text()
    native_states = set(re.findall(r'__write_dialog_status\(\s*"([a-z_]+)"', source))
    assert {"processing_audio", "followup_preparing", "connected", "proactive_queued",
            "proactive_speaking", "proactive_finishing", "stopping"} <= native_states
    assert native_states <= set(get_args(State))


@pytest.mark.asyncio
async def test_opaque_scoped_list_detail_and_incremental_full_replacement():
    device = Upstream()
    bridge = reader(device)
    listing = await bridge.read(user_id="usr", pet_id="pet", operation="list")
    conversation = listing["items"][0]["conversationId"]
    assert conversation.startswith("conv_") and "native" not in conversation
    same = await bridge.read(user_id="usr", pet_id="pet", operation="list", revision=listing["revision"])
    assert same["unchanged"] is True and same["items"] == []
    detail = await bridge.read(user_id="usr", pet_id="pet", operation="detail", conversation_id=conversation)
    assert len(detail["messages"]) == 2 and detail["scope"] == "persisted_only"
    assert detail["messages"][0]["messageId"].startswith("msg_") and "raw-message" not in json.dumps(detail)
    unchanged = await bridge.read(user_id="usr", pet_id="pet", operation="detail", conversation_id=conversation, revision=detail["revision"])
    assert unchanged["unchanged"] and unchanged["messages"] == []
    device.summary["assistant_preview"] = "新内容"
    changed = await bridge.read(user_id="usr", pet_id="pet", operation="detail", conversation_id=conversation, revision=detail["revision"])
    assert not changed["unchanged"] and len(changed["messages"]) == 2
    with pytest.raises(DialogReadError) as error:
        await bridge.read(user_id="other", pet_id="pet", operation="detail", conversation_id=conversation)
    assert error.value.status_code == 404
    with pytest.raises(DialogReadError):
        await reader(device).read(user_id="usr", pet_id="pet", operation="detail", conversation_id=conversation)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["owner", "serial", "capability"])
async def test_owner_pin_and_new_capability_are_required_after_every_read(change):
    device = Upstream()
    device.during = lambda: setattr(device, change, "other_value")
    with pytest.raises(DialogReadError):
        await reader(device).read(user_id="usr", pet_id="pet", operation="status")


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["unknown_state", "boolean_sequence", "nonfinite", "path_id", "control_text"])
async def test_bad_upstream_is_not_guessed_or_shown(invalid):
    device = Upstream()
    if invalid == "unknown_state":
        device.status["state"] = "custom raw error"
    elif invalid == "boolean_sequence":
        device.status["sequence"] = True
    elif invalid == "nonfinite":
        device.status["sequence"] = float("inf")
    elif invalid == "path_id":
        device.summary["conversation_id"] = "../../admin/reset"
    else:
        device.summary["preview"] = "hello\u202eunsafe"
    with pytest.raises(DialogReadError):
        await reader(device).read(user_id="usr", pet_id="pet", operation="status" if invalid in {"unknown_state", "boolean_sequence", "nonfinite"} else "list")


class Provider:
    async def exchange(self, code):
        return code


@pytest.fixture
def gateway(tmp_path):
    store = SessionStore(tmp_path / "session.sqlite3")
    device = Upstream()
    bridge = reader(device)
    config = MiniGatewayConfig(db_path=store.path, app_id="wx1234567890abcdef", app_secret="a" * 32, status_pet_id="pet_mini")
    status = type("Reader", (), {"expected_serial": SERIAL})()
    app = create_app(config, provider=Provider(), store=store, status_reader=status, dialog_reader=bridge)
    with TestClient(app) as client:
        identity = client.post("/v1/auth/wechat", json={"code": "owner"}).json()["data"]
        store.grant_pet(identity["userId"], "pet_mini", "小安")
        with store._connect() as connection:
            connection.execute("INSERT INTO mg_meta VALUES(?,?)", ("device:pet_mini", hashlib.sha256(SERIAL.encode()).hexdigest()))
        yield client, bridge, device, store, identity, {"Authorization": "Bearer " + identity["accessToken"]}


def test_dialog_routes_are_get_only_authorized_and_query_body_free(gateway):
    client, _, device, _, _, headers = gateway
    base = "/v1/pets/pet_mini/dialog"
    assert client.get(base).status_code == 401
    assert client.get(base.replace("pet_mini", "other"), headers=headers).status_code == 404
    assert client.get(base + "?url=unsafe", headers=headers).status_code == 400
    assert client.request("GET", base, headers=headers, content=b"x").status_code == 400
    assert client.post(base, headers=headers).status_code == 405
    listing = client.get(base + "/conversations", headers=headers).json()["data"]
    revision = listing["revision"]
    assert client.get(base + "/conversations/since/" + revision, headers=headers).json()["data"]["unchanged"]
    conversation = listing["items"][0]["conversationId"]
    detail = client.get(base + "/conversations/" + conversation, headers=headers).json()["data"]
    assert client.get(base + "/conversations/" + conversation + "/since/" + detail["revision"], headers=headers).json()["data"]["unchanged"]
    assert client.get(base + "/conversations/raw-upstream-id", headers=headers).status_code == 400
    assert all(request.method == "GET" for request in device.requests)


@pytest.mark.parametrize("change,expected", [("session", 401), ("owner", 404), ("pin", 503)])
def test_late_read_never_escapes_mini_authorization_revocation(gateway, change, expected):
    client, _, device, store, identity, headers = gateway
    def revoke():
        if change == "session":
            store.revoke_session(identity["accessToken"], "wx1234567890abcdef")
        elif change == "owner":
            store.revoke_pet(identity["userId"], "pet_mini")
        else:
            with store._connect() as connection:
                connection.execute("DELETE FROM mg_meta WHERE key='device:pet_mini'")
    device.during = revoke
    response = client.get("/v1/pets/pet_mini/dialog", headers=headers)
    assert response.status_code == expected
    assert "sequence" not in response.text


def _bound(client):
    identity = client.post("/api/v1/auth/mock-login", json={"login_code": "readonly-test-owner", "nickname": "只读测试"}).json()["data"]
    headers = {"X-Mock-Session": identity["session_token"]}
    bound = client.post("/api/v1/pets/bind", headers=headers,
        json={"device_serial": SERIAL, "pet_name": "小安", "network_name": "Mock"}).json()["data"]
    return identity, headers, bound["pet"]["pet_id"]


def test_dedicated_upstream_reads_only_select_and_never_sync_memory_growth(client, monkeypatch, tmp_path):
    identity, headers, pet = _bound(client)
    service = client.app.state.services.product
    repo = service._repository
    conversation, _ = repo.add_dialog_pair(user_id=identity["user"]["user_id"], pet_id=pet,
        user_content="测试问题", assistant_content="测试回答", voice_id="volcengine_tts")
    async def forbidden(*args, **kwargs):
        raise AssertionError("Must not import/sync events from read-only endpoint")
    monkeypatch.setattr(service, "_sync_native_dialog_events", forbidden)
    events = tmp_path / "known-events.jsonl"
    runtime = tmp_path / "known-personality.json"
    events.write_text('{"test_fixture":"pending native event, do not import"}\n')
    runtime.write_text('{"test_fixture":"unchanged runtime"}\n')
    monkeypatch.setattr(service, "_settings", service._settings.model_copy(update={
        "dialog_event_path": events, "personality_runtime_path": runtime}))
    before = {path: hashlib.sha256(path.read_bytes()).digest() for path in (events, runtime)}
    statements = []
    original = repo._connect_readonly
    def connect():
        connection = original()
        connection.set_trace_callback(statements.append)
        return connection
    monkeypatch.setattr(repo, "_connect_readonly", connect)
    base = "/api/v1/pets/" + pet + "/dialog-readonly/"
    for suffix in ("status", "conversations", "conversations/" + conversation):
        response = client.get(base + suffix, headers=headers)
        assert response.status_code == 200
        assert response.json()["data"]["capability"] == CAPABILITY
        assert client.get(base + suffix + "?sync=true", headers=headers).status_code == 400
        assert client.request("GET", base + suffix, headers=headers, content=b"x").status_code == 400
    assert before == {path: hashlib.sha256(path.read_bytes()).digest() for path in (events, runtime)}
    assert statements and all(statement.lstrip().upper().startswith(("SELECT", "WITH", "PRAGMA QUERY_ONLY=ON", "BEGIN", "COMMIT", "ROLLBACK")) for statement in statements)
    other, wrong, _ = _bound_with_other(client)
    assert client.get(base + "status", headers=wrong).status_code == 404


def _bound_with_other(client):
    identity = client.post("/api/v1/auth/mock-login", json={"login_code": "other-readonly-owner", "nickname": "其他"}).json()["data"]
    return identity, {"X-Mock-Session": identity["session_token"]}, None


@pytest.mark.asyncio
async def test_real_upstream_api_and_repository_apply_bounded_persisted_window(client):
    identity, headers, pet = _bound(client)
    repo = client.app.state.services.product._repository
    with repo._connect() as connection:
        for index in range(105):
            connection.execute("INSERT INTO dialog_history(message_id,conversation_id,pet_id,user_id,role,content,voice_id,created_at) VALUES(?,?,?,?,?,?,?,?)",
                ("source-message-" + str(index), "native-bounded-conversation", pet, identity["user"]["user_id"],
                 "user" if index % 2 == 0 else "assistant", "内容" + str(index), "volcengine_tts", STAMP))
    real = httpx.ASGITransport(app=client.app)
    device = Upstream()
    async def transport(request):
        if request.url.path in {"/health", "/api/v1/device/status"}:
            return await device(request)
        return await real.handle_async_request(request)
    bridge = LocalDialogReader(upstream_url="http://192.168.1.152:8000", api_key="test-private-key",
        expected_serial=SERIAL, product_session=identity["session_token"], product_pet_id=pet,
        transport=httpx.MockTransport(transport))
    listing = await bridge.read(user_id="usr_mini", pet_id="pet_mini", operation="list")
    item = listing["items"][0]
    assert item["messageCount"] == 105
    detail = await bridge.read(user_id="usr_mini", pet_id="pet_mini", operation="detail", conversation_id=item["conversationId"])
    assert detail["truncated"] and len(detail["messages"]) == 100
    assert detail["messages"][0]["content"] == "内容5" and detail["messages"][-1]["content"] == "内容104"
    assert "native-bounded-conversation" not in json.dumps(detail)


@pytest.mark.asyncio
async def test_chinese_long_messages_use_complete_bounded_suffix_and_keep_incremental_count():
    device = Upstream()
    original = device.__call__
    async def transport(request):
        response = await original(request)
        if request.url.path.endswith("/native-raw-conversation"):
            payload = response.json()
            data = payload["data"]
            data["conversation"].update(message_count=100, user_message_count=50, assistant_message_count=50)
            data["messages"] = [{"message_id": "long-message-" + str(index), "conversation_id": "native-raw-conversation",
                "role": "user" if index % 2 == 0 else "assistant", "content": "汉" * 8192,
                "created_at": STAMP} for index in range(100)]
            return httpx.Response(200, json=payload)
        return response
    bridge = reader(device)
    bridge._transport = httpx.MockTransport(transport)
    listing = await bridge.read(user_id="usr", pet_id="pet", operation="list")
    conversation = listing["items"][0]["conversationId"]
    detail = await bridge.read(user_id="usr", pet_id="pet", operation="detail", conversation_id=conversation)
    assert detail["windowMessageCount"] == 8 and detail["truncated"]
    assert len(detail["messages"]) == 8 and all(len(message["content"]) == 8192 for message in detail["messages"])
    assert sum(len(message["content"].encode("utf-8")) for message in detail["messages"]) == 192 * 1024
    assert len(json.dumps(detail, ensure_ascii=False).encode("utf-8")) < 256 * 1024
    unchanged = await bridge.read(user_id="usr", pet_id="pet", operation="detail", conversation_id=conversation,
                                  revision=detail["revision"])
    assert unchanged["windowMessageCount"] == 8 and unchanged["truncated"] and unchanged["messages"] == []


@pytest.mark.asyncio
async def test_close_during_flight_and_cross_owner_results_are_rejected():
    device = Upstream()
    bridge = reader(device)
    device.during = bridge.close
    with pytest.raises(DialogReadError):
        await bridge.read(user_id="usr", pet_id="pet", operation="status")


@pytest.mark.asyncio
async def test_failed_initial_capability_does_not_publish_an_owner_pin():
    device = Upstream()
    bridge = reader(device)
    device.capability = "old importing endpoint"
    with pytest.raises(DialogReadError):
        await bridge.read(user_id="usr", pet_id="pet", operation="status")
    assert bridge._owner is None
    device.owner = "correct_explicit_owner"
    device.capability = CAPABILITY
    await bridge.read(user_id="usr", pet_id="pet", operation="status")
    assert bridge._owner == "correct_explicit_owner"


def test_unconfigured_dialog_routes_refuse_instead_of_using_old_importer(tmp_path):
    store = SessionStore(tmp_path / "sessions.sqlite3")
    config = MiniGatewayConfig(db_path=store.path, app_id="wx1234567890abcdef", app_secret="a" * 32, status_pet_id="pet_mini")
    with TestClient(create_app(config, provider=Provider(), store=store)) as client:
        identity = client.post("/v1/auth/wechat", json={"code": "owner"}).json()["data"]
        store.grant_pet(identity["userId"], "pet_mini", "小安")
        response = client.get("/v1/pets/pet_mini/dialog", headers={"Authorization": "Bearer " + identity["accessToken"]})
        assert response.status_code == 503


def test_true_readonly_connection_does_not_create_a_missing_database(tmp_path):
    path = tmp_path / "missing.sqlite3"
    repo = SQLiteRepository(path)
    with pytest.raises(sqlite3.OperationalError):
        repo.get_owned_pet("usr", "pet", read_only=True)
    with pytest.raises(sqlite3.OperationalError):
        repo.list_dialog_conversations("usr", "pet", read_only=True)
    with pytest.raises(sqlite3.OperationalError):
        repo.get_dialog_conversation_readonly("usr", "pet", "conv_one")
    assert not path.exists()


def test_readonly_uri_blocks_writes_even_if_query_only_is_disabled(client):
    identity, _, pet = _bound(client)
    repo = client.app.state.services.product._repository
    connection = repo._connect_readonly()
    try:
        connection.execute("PRAGMA query_only=OFF")
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("UPDATE pets SET name='must not change' WHERE pet_id=?", (pet,))
    finally:
        connection.close()


@pytest.mark.parametrize("mutation", ["append", "delete"])
def test_readonly_detail_counts_and_window_share_one_snapshot_during_concurrent_change(client, monkeypatch, mutation):
    identity, _, pet = _bound(client)
    repo = client.app.state.services.product._repository
    owner = identity["user"]["user_id"]
    conversation, messages = repo.add_dialog_pair(user_id=owner, pet_id=pet,
        user_content="问题", assistant_content="回答", voice_id="volcengine_tts")
    original = repo._connect_readonly
    changed = False

    class Cursor:
        def __init__(self, cursor):
            self.cursor = cursor
        def fetchone(self):
            nonlocal changed
            row = self.cursor.fetchone()
            # Mutate from another connection after the aggregate has returned,
            # but before the bounded message SELECT runs. WAL permits the writer
            # to commit while this reader retains its earlier snapshot.
            if not changed:
                changed = True
                with repo._connect() as writer:
                    if mutation == "append":
                        writer.execute("INSERT INTO dialog_history(message_id,conversation_id,pet_id,user_id,role,content,voice_id,created_at) VALUES(?,?,?,?,?,?,?,?)",
                            ("concurrent-answer", conversation, pet, owner, "assistant", "稍后追加的回答", "volcengine_tts", STAMP))
                    else:
                        writer.execute("DELETE FROM dialog_history WHERE message_id=?", (messages[-1]["message_id"],))
            return row

    class Connection:
        def __init__(self):
            self.connection = original()
            assert self.connection.in_transaction
        def __enter__(self):
            self.connection.__enter__()
            return self
        def __exit__(self, *args):
            try:
                return self.connection.__exit__(*args)
            finally:
                self.connection.close()
        def execute(self, sql, parameters=()):
            cursor = self.connection.execute(sql, parameters)
            return Cursor(cursor) if "WITH messages AS" in sql else cursor

    monkeypatch.setattr(repo, "_connect_readonly", Connection)
    result = repo.get_dialog_conversation_readonly(owner, pet, conversation)
    assert changed and result["summary"]["message_count"] == 2
    assert len(result["messages"]) == 2 and result["truncated"] is False
    assert [message["content"] for message in result["messages"]] == ["问题", "回答"]
    with repo._connect() as writer:
        count = writer.execute("SELECT COUNT(*) FROM dialog_history WHERE conversation_id=?", (conversation,)).fetchone()[0]
    assert count == (3 if mutation == "append" else 1)
