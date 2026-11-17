"""Dialog commands use synthetic HTTP and temporary private SQLite only."""

import asyncio
from contextlib import closing
import hashlib
import json
import os
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from ai_cat_controller.mini_gateway import dialog_control
from ai_cat_controller.mini_gateway.app import create_app
from ai_cat_controller.mini_gateway.config import MiniGatewayConfig
from ai_cat_controller.mini_gateway.dialog import LocalDialogReader
from ai_cat_controller.mini_gateway.dialog_control import DialogControlError, LocalDialogController
from ai_cat_controller.mini_gateway.store import SessionStore

SERIAL = "K1-CONTROL-SYNTHETIC"
PET = "pet_web_synthetic"
OWNER = "usr_web_synthetic"
WEB = "synthetic_private_web_session"
KEY = "synthetic_private_device_key"


class Device:
    def __init__(self):
        self.requests, self.post_hook = [], None
        self.serial, self.owner, self.capability = SERIAL, OWNER, "persisted_dialog_readonly_v1"
        self.status = {"state": "ready", "stale": False, "session_active": False,
                       "updated_at_ms": 1792548000000, "sequence": 1}
        self.read_hook = lambda: None

    async def __call__(self, request):
        self.requests.append(request)
        if request.method == "POST":
            if self.post_hook:
                return await self.post_hook(request)
            body = json.loads(request.content)
            state = {"/api/v1/dialog/wake": "awake", "/api/v1/dialog/interrupt": "interrupted", "/api/v1/dialog/text": "queued"}[request.url.path]
            return httpx.Response(200, json={"success": True, "data": {
                "request_id": body["request_id"], "dialog_state": state, "changed": True}})
        if request.url.path == "/health":
            data = {"status": "ok", "adapter": "local_k1"}
        elif request.url.path == "/api/v1/device/status":
            data = {"adapter_mode": "local_k1", "connected": True, "device_serial": self.serial}
        elif request.url.path == "/api/v1/pets":
            data = [{"pet_id": PET, "owner_user_id": self.owner, "serial_number": SERIAL, "online": True}]
        elif request.url.path == "/api/v1/pets/" + PET + "/dialog-readonly/status":
            self.read_hook()
            data = {"read_only": True, "capability": self.capability, "status": self.status}
        else:
            pytest.fail("Unexpected or writable upstream path")
        return httpx.Response(200, json={"success": True, "data": data})

    @property
    def posts(self):
        return [request for request in self.requests if request.method == "POST"]


def controller(tmp_path, device=None, **kwargs):
    device = device or Device()
    values = dict(db_path=tmp_path / "private" / "dialog.sqlite3", enabled=True,
        upstream_url="http://192.168.1.152:8000", api_key=KEY, expected_serial=SERIAL,
        product_session=WEB, product_pet_id=PET, transport=httpx.MockTransport(device))
    values.update(kwargs)
    return LocalDialogController(**values)


async def authorize():
    pass


def arguments(kind="text", request="one"):
    result = {"user_id": "usr_mini", "pet_id": "pet_mini", "request_id": request, "kind": kind, "authorize": authorize}
    if kind == "text":
        result["content"] = "合成问题，不保存正文"
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["listen", "text", "interrupt"])
async def test_fixed_ack_is_only_acceptance_and_same_id_never_resends(tmp_path, kind):
    device = Device()
    client = controller(tmp_path, device)
    receipt = await client.submit(**arguments(kind))
    assert receipt["status"] == "accepted" and receipt["kind"] == kind
    assert await client.submit(**arguments(kind)) == receipt
    assert await client.receipt(user_id="usr_mini", pet_id="pet_mini", request_id="one") == receipt
    assert len(device.posts) == 1
    post = device.posts[0]
    assert post.url.path in {"/api/v1/dialog/wake", "/api/v1/dialog/text", "/api/v1/dialog/interrupt"}
    assert "X-Mock-Session" not in post.headers and post.headers["X-API-Key"] == KEY
    body = json.loads(post.content)
    assert body["request_id"] != "one" and len(body["request_id"]) <= 64
    with closing(client._connect()) as connection:
        saved = "\n".join(connection.iterdump())
    assert all(value not in saved for value in ["合成问题", KEY, WEB])
    assert all(value not in json.dumps(receipt) for value in [PET, OWNER, KEY, WEB, "content"])
    client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("state,stale", [("unavailable", False), ("thinking", False), ("processing_audio", False),
    ("followup_preparing", False), ("proactive_speaking", False), ("stopping", False), ("ready", True)])
async def test_stale_unavailable_and_native_busy_states_keep_interrupt_independent(tmp_path, state, stale):
    device = Device()
    device.status.update(state=state, stale=stale)
    client = controller(tmp_path, device)
    snapshot = await client.snapshot(user_id="usr", pet_id="pet")
    assert snapshot["verified"] and not snapshot["canListen"] and not snapshot["canSendText"]
    assert snapshot["canInterrupt"]
    for kind in ("listen", "text"):
        with pytest.raises(DialogControlError):
            await client.submit(**arguments(kind, "blocked-" + kind))
    assert not device.posts
    assert (await client.submit(**arguments("interrupt")))["status"] == "accepted"
    client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["id", "changed_type", "wrong_state", "extra", "status", "timeout", "oversize"])
async def test_unknown_delivery_latches_durably_and_interrupt_does_not_clear(tmp_path, bad):
    device = Device()
    async def bad_ack(request):
        if bad == "timeout":
            raise httpx.ReadTimeout("Must not expose " + KEY)
        data = {"request_id": json.loads(request.content)["request_id"], "changed": True, "dialog_state": "queued"}
        if bad == "id":
            data["request_id"] = "wrong"
        elif bad == "changed_type":
            data["changed"] = 1
        elif bad == "wrong_state":
            data["dialog_state"] = "answered"
        elif bad == "extra":
            data["private"] = KEY
        elif bad == "oversize":
            return httpx.Response(200, content=b"x" * 20_000)
        return httpx.Response(500 if bad == "status" else 200, json={"success": True, "data": data})
    device.post_hook = bad_ack
    client = controller(tmp_path, device)
    receipt = await client.submit(**arguments())
    assert receipt["status"] == "unknown"
    client.close()
    client = controller(tmp_path, device)
    assert (await client.submit(**arguments()))["status"] == "unknown" and len(device.posts) == 1
    for user in ("usr_mini", "reassigned_owner"):
        with pytest.raises(DialogControlError) as error:
            await client.submit(**{**arguments("listen", "new-id"), "user_id": user})
        assert error.value.code == "DIALOG_REVIEW_REQUIRED"
    device.post_hook = None
    assert (await client.submit(**arguments("interrupt", "stop")))["status"] == "accepted"
    assert (await client.snapshot(user_id="usr_mini", pet_id="pet_mini"))["manualReviewRequired"]
    assert len(device.posts) == 2
    with pytest.raises(DialogControlError) as error:
        await client.receipt(user_id="other", pet_id="pet_mini", request_id="one")
    assert error.value.status_code == 404
    client.close()


@pytest.mark.asyncio
async def test_inflight_intent_is_committed_and_cancelled_caller_cannot_duplicate_dispatch(tmp_path):
    device = Device()
    started, release = asyncio.Event(), asyncio.Event()
    client = controller(tmp_path, device)
    async def deferred(request):
        assert client._find("usr_mini", "pet_mini", "one")["status"] == "sending"
        started.set()
        await release.wait()
        return httpx.Response(200, json={"success": True, "data": {"request_id": json.loads(request.content)["request_id"],
            "changed": True, "dialog_state": "queued"}})
    device.post_hook = deferred
    first = asyncio.create_task(client.submit(**arguments()))
    await started.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert (await client.submit(**arguments()))["status"] == "sending"
    assert len(device.posts) == 1
    with pytest.raises(DialogControlError) as error:
        await client.submit(**arguments("text", "second"))
    assert error.value.code == "DIALOG_COMMAND_BUSY"
    release.set()
    await asyncio.gather(*list(client._tasks.values()))
    assert (await client.receipt(user_id="usr_mini", pet_id="pet_mini", request_id="one"))["status"] == "accepted"
    assert len(device.posts) == 1
    client.close()


@pytest.mark.asyncio
async def test_deferred_interrupt_blocks_new_normal_commands_but_can_interrupt_existing_normal(tmp_path):
    device = Device()
    started, release = asyncio.Event(), asyncio.Event()
    async def stop(request):
        started.set()
        await release.wait()
        return httpx.Response(200, json={"success": True, "data": {"request_id": json.loads(request.content)["request_id"],
            "changed": True, "dialog_state": "interrupted"}})
    device.post_hook = stop
    client = controller(tmp_path, device)
    task = asyncio.create_task(client.submit(**arguments("interrupt", "stop")))
    await started.wait()
    snapshot = await client.snapshot(user_id="usr", pet_id="pet")
    assert not snapshot["canListen"] and not snapshot["canSendText"] and not snapshot["canInterrupt"]
    for kind in ("text", "listen"):
        with pytest.raises(DialogControlError) as error:
            await client.submit(**arguments(kind, "new-" + kind))
        assert error.value.code == "DIALOG_COMMAND_BUSY"
    assert len(device.posts) == 1
    release.set()
    assert (await task)["status"] == "accepted"
    client.close()


@pytest.mark.asyncio
async def test_interrupt_arriving_during_normal_probe_prevents_later_normal_dispatch(tmp_path):
    device = Device()
    normal_read, resume_normal, stop_post, finish_stop = (asyncio.Event() for _ in range(4))
    original = device.__call__
    calls = 0
    async def transport(request):
        nonlocal calls
        if request.method == "GET" and "/dialog-readonly/status" in request.url.path:
            calls += 1
            if calls == 1:
                normal_read.set()
                await resume_normal.wait()
        return await original(request)
    async def interrupt(request):
        assert request.url.path.endswith("/interrupt")
        stop_post.set()
        await finish_stop.wait()
        return httpx.Response(200, json={"success": True, "data": {"request_id": json.loads(request.content)["request_id"],
            "changed": True, "dialog_state": "interrupted"}})
    device.post_hook = interrupt
    client = controller(tmp_path, transport=httpx.MockTransport(transport))
    normal = asyncio.create_task(client.submit(**arguments()))
    await normal_read.wait()
    stop = asyncio.create_task(client.submit(**arguments("interrupt", "stop")))
    await stop_post.wait()
    resume_normal.set()
    result = await normal
    assert result["status"] == "not_sent" and len(device.posts) == 1
    finish_stop.set()
    assert (await stop)["status"] == "accepted"
    client.close()


@pytest.mark.asyncio
async def test_startup_recovers_sending_as_unknown_without_dispatch(tmp_path):
    device = Device()
    client = controller(tmp_path, device)
    with closing(client._connect()) as connection, connection:
        connection.execute("INSERT INTO dc_requests VALUES('usr_mini','pet_mini','one','listen','digest','sending',?)", (dialog_control._utc(),))
    client.close()
    client = controller(tmp_path, device)
    result = await client.receipt(user_id="usr_mini", pet_id="pet_mini", request_id="one")
    assert result["status"] == "unknown" and client._review() and not device.requests
    with pytest.raises(DialogControlError):
        await client.submit(**arguments("listen", "new"))
    assert not device.posts
    client.close()


@pytest.mark.asyncio
async def test_http_client_entry_cannot_create_an_authorization_or_deadline_gap(tmp_path, monkeypatch):
    device = Device()
    client = controller(tmp_path, device)
    original = httpx.AsyncClient.__aenter__
    revoked = False
    async def enter(http_client):
        nonlocal revoked
        if http_client.timeout.read == dialog_control._ACK_TIMEOUT:
            revoked = True
        return await original(http_client)
    async def authorized():
        if revoked:
            raise RuntimeError("revoked after HTTP-client entry")
    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", enter)
    with pytest.raises(RuntimeError):
        await client.submit(**{**arguments(), "authorize": authorized})
    assert not device.posts
    assert (await client.receipt(user_id="usr_mini", pet_id="pet_mini", request_id="one"))["status"] == "not_sent"
    client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["capability", "voice_phase"])
async def test_http_client_entry_rechecks_version_and_phase_before_dispatch(tmp_path, monkeypatch, change):
    device = Device()
    client = controller(tmp_path, device)
    original = httpx.AsyncClient.__aenter__
    async def enter(http_client):
        if http_client.timeout.read == dialog_control._ACK_TIMEOUT:
            if change == "capability":
                device.capability = "removed-readonly-capability"
            else:
                device.status["state"] = "thinking"
        return await original(http_client)
    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", enter)
    result = await client.submit(**arguments())
    assert result["status"] == "not_sent" and not device.posts
    assert (await client.receipt(user_id="usr_mini", pet_id="pet_mini", request_id="one"))["status"] == "not_sent"
    assert not client._review()
    client.close()


@pytest.mark.asyncio
async def test_late_authorization_prevents_dispatch_and_retains_not_sent_receipt(tmp_path):
    device = Device()
    client = controller(tmp_path, device)
    calls = 0
    async def revoke():
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("authorization revoked")
    with pytest.raises(RuntimeError):
        await client.submit(**{**arguments(), "authorize": revoke})
    assert not device.posts
    assert (await client.receipt(user_id="usr_mini", pet_id="pet_mini", request_id="one"))["status"] == "not_sent"
    assert not client._review()
    client.close()


@pytest.mark.asyncio
async def test_conflicting_request_kind_or_content_never_resends(tmp_path):
    device = Device()
    client = controller(tmp_path, device)
    await client.submit(**arguments())
    for changes in ({"kind": "listen", "content": None}, {"content": "不同正文"}):
        with pytest.raises(DialogControlError) as error:
            await client.submit(**{**arguments(), **changes})
        assert error.value.code == "DIALOG_COMMAND_CONFLICT"
    assert len(device.posts) == 1
    client.close()


@pytest.mark.asyncio
async def test_failed_post_delivery_receipt_write_does_not_reopen_new_normal_commands(tmp_path, monkeypatch):
    device = Device()
    client = controller(tmp_path, device)
    original = client._update
    def cannot_save(user, pet, request, state):
        if state == "accepted":
            raise sqlite3.OperationalError("synthetic disk full")
        return original(user, pet, request, state)
    monkeypatch.setattr(client, "_update", cannot_save)
    with pytest.raises(sqlite3.OperationalError):
        await client.submit(**arguments())
    assert len(device.posts) == 1
    assert (await client.receipt(user_id="usr_mini", pet_id="pet_mini", request_id="one"))["status"] == "sending"
    assert (await client.snapshot(user_id="usr_mini", pet_id="pet_mini"))["manualReviewRequired"]
    with pytest.raises(DialogControlError) as error:
        await client.submit(**arguments("listen", "new"))
    assert error.value.code == "DIALOG_REVIEW_REQUIRED" and len(device.posts) == 1
    client.close()


def test_private_store_lock_schema_and_pin_guard_before_existing_database_writes(tmp_path):
    client = controller(tmp_path)
    with pytest.raises(ValueError):
        controller(tmp_path)
    client.close()
    with pytest.raises(ValueError):
        controller(tmp_path, expected_serial="ANOTHER-SYNTHETIC")
    path = tmp_path / "precious.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE precious(value TEXT)")
    path.chmod(0o600)
    before = path.read_bytes()
    with pytest.raises(ValueError):
        controller(tmp_path, db_path=path)
    assert path.read_bytes() == before


@pytest.mark.parametrize("unsafe", ["symlink", "hardlink", "public", "wal"])
def test_unsafe_store_files_are_rejected(tmp_path, unsafe):
    client = controller(tmp_path)
    path = client.path
    client.close()
    if unsafe == "symlink":
        original = path.with_name("original.sqlite3")
        path.rename(original)
        path.symlink_to(original)
    elif unsafe == "hardlink":
        os.link(path, path.with_name("alias.sqlite3"))
    elif unsafe == "public":
        path.chmod(0o644)
    else:
        path.with_name(path.name + "-wal").write_bytes(b"private fixture")
    with pytest.raises(ValueError):
        controller(tmp_path)


class Provider:
    async def exchange(self, code):
        return code


@pytest.fixture
def gateway(tmp_path):
    device = Device()
    control = controller(tmp_path, device)
    read = control._reader
    store = SessionStore(tmp_path / "sessions.sqlite3")
    config = MiniGatewayConfig(db_path=store.path, app_id="wx1234567890abcdef", app_secret="a" * 32, status_pet_id="pet_mini")
    status = type("Status", (), {"expected_serial": SERIAL})()
    app = create_app(config, provider=Provider(), store=store, status_reader=status, dialog_reader=read, dialog_controller=control)
    with TestClient(app) as client:
        identity = client.post("/v1/auth/wechat", json={"code": "owner"}).json()["data"]
        store.grant_pet(identity["userId"], "pet_mini", "小安")
        with store._connect() as connection:
            connection.execute("INSERT INTO mg_meta VALUES(?,?)", ("device:pet_mini", hashlib.sha256(SERIAL.encode()).hexdigest()))
        yield client, control, device, store, identity, {"Authorization": "Bearer " + identity["accessToken"]}
    control.close()


def test_routes_require_auth_no_query_and_only_command_body_gets_4096_budget(gateway):
    client, _, device, _, _, headers = gateway
    base = "/v1/pets/pet_mini/dialog"
    assert client.get(base + "/control").status_code == 401
    assert client.post(base + "/commands", json={"requestId": "one", "kind": "listen"}).status_code == 401
    assert client.get(base + "/control?token=bad", headers=headers).status_code == 400
    assert client.request("GET", base + "/control", headers=headers, content=b"x" * 1025).status_code == 413
    assert client.post(base + "/commands", headers=headers, content=b"x" * 4097).status_code == 413
    response = client.post(base + "/commands", headers=headers, json={"requestId": "one", "kind": "text", "content": "汉" * 500})
    assert response.status_code == 200 and response.json()["data"]["status"] == "accepted"
    assert len(device.posts) == 1
    checked = client.get(base + "/requests/one", headers=headers)
    assert checked.status_code == 200 and checked.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("body", [{"requestId": "one", "kind": "listen", "content": None},
    {"requestId": "one", "kind": "interrupt", "content": "bad"},
    {"requestId": "one", "kind": "text"}, {"requestId": "one", "kind": "text", "content": " "},
    {"requestId": "one", "kind": "text", "content": "line\nline"},
    {"requestId": "one", "kind": "text", "content": "x" * 501},
    {"requestId": "one", "kind": "text", "content": "hello\u202e"},
    {"requestId": "bad/id", "kind": "listen"}, {"requestId": "one", "kind": "speak"},
    {"requestId": "one", "kind": "listen", "url": "http://unsafe"}])
def test_fixed_command_schema_rejects_extra_fields_and_bad_text(gateway, body):
    client, _, device, _, _, headers = gateway
    assert client.post("/v1/pets/pet_mini/dialog/commands", headers=headers, json=body).status_code == 400
    assert not device.posts


@pytest.mark.parametrize("change,expected", [("session", 401), ("pet", 404), ("pin", 503)])
def test_late_mini_authorization_is_rechecked_before_command_dispatch(gateway, change, expected):
    client, control, device, store, identity, headers = gateway
    def revoke():
        if change == "session":
            store.revoke_session(identity["accessToken"], "wx1234567890abcdef")
        elif change == "pet":
            store.revoke_pet(identity["userId"], "pet_mini")
        else:
            with store._connect() as connection:
                connection.execute("DELETE FROM mg_meta WHERE key='device:pet_mini'")
    device.read_hook = revoke
    response = client.post("/v1/pets/pet_mini/dialog/commands", headers=headers,
        json={"requestId": "one", "kind": "listen"})
    assert response.status_code == expected and not device.posts
