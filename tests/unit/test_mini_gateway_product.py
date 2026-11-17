"""No network or hardware: bounded product reads and at-most-once receipts."""

import asyncio
from contextlib import closing
from copy import deepcopy
import hashlib
import json
import os
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from ai_cat_controller.domain.intimacy import INTIMACY_LEVELS, INTERACTION_RULES, level_progress
from ai_cat_controller.mini_gateway.app import create_app
from ai_cat_controller.mini_gateway.config import MiniGatewayConfig
from ai_cat_controller.mini_gateway.product import LocalProductService, ProductError
from ai_cat_controller.mini_gateway import product
from ai_cat_controller.mini_gateway.store import SessionStore

SERIAL = "7c2b63fd4a128"
PET = "pet_upstream"
TOKEN = "private_product_authorization"
OWNER = "usr_existing_owner"
KEY = "private-device-key"
TIME = "2026-10-21T02:00:00+00:00"


def raw_intimacy():
    return {"points": 3, "level": INTIMACY_LEVELS[0].model_dump(), "progress": level_progress(3),
            "address": "新朋友", "daily_growth_cap": 20, "daily_check_in_completed": False,
            "debug_unlimited_touch_intimacy": False,
            "rules": [item.model_dump() for item in INTERACTION_RULES.values()],
            "history": [{"event_type": "touch", "points_delta": 1, "points_after": 3,
                         "created_at": TIME, "event_id": "private_event", "request_id": "private_request"}]}


def raw_growth():
    ids = [("curiosity", "好奇"), ("empathy", "共情"), ("knowledge", "求知"),
           ("energy", "活力"), ("mischief", "淘气"), ("discipline", "自律")]
    return {"enabled": True, "attributes": {key: {"label": label, "tendency": "普通", "value": 0.25}
                                              for key, label in ids},
            "active_tags": [], "tag_history": [], "recent_events": [{"event_id": "private_event",
                "event_type": "physical_touch", "event_label": "实体触摸", "topic": "touch", "emotion": "positive",
                "changed_attributes": ["共情", "活力"], "created_at": TIME, "attribute_delta": {"empathy": 99}}],
            "behavior_profile": {"base_personality_id": "gentle_companion", "base_personality_name": "温柔陪伴者",
                "intimacy_level": 0, "active_tag_names": [], "directives": [], "revision": "private_revision"},
            "debug_values_visible": True}


class Device:
    def __init__(self):
        self.serial, self.owner = SERIAL, OWNER
        self.policy = {}
        self.requests, self.post_hook = [], None
        self.intimacy = raw_intimacy()
        self.payload = {"pet": {"name": "小安", "pet_id": PET, "owner_user_id": OWNER},
            "personality": {"personality_id": "gentle_companion", "name": "温柔陪伴者", "description": "陪伴",
                "language_style": "温柔", "voice_name": "测试声音", "voice_id": "private_voice"},
            "intimacy": raw_intimacy(), "unlocked_action_ids": ["celebration_combo"]}
        self.growth = raw_growth()

    async def __call__(self, request):
        self.requests.append(request)
        path = request.url.path
        if path == "/health":
            data = {"status": "ok", "adapter": "local_k1"}
        elif path == "/api/v1/device/status":
            data = {"adapter_mode": "local_k1", "connected": True, "device_serial": self.serial, **self.policy}
        elif path == "/api/v1/pets":
            data = [{"pet_id": PET, "owner_user_id": self.owner, "serial_number": SERIAL, "online": True}]
        elif path.endswith("/dashboard"):
            data = self.payload
        elif path.endswith("/intimacy"):
            data = self.intimacy
        elif path.endswith("/growth"):
            data = self.growth
        elif path.endswith("/interactions") and request.method == "POST":
            if self.post_hook:
                return await self.post_hook(request)
            body = json.loads(request.content)
            data = {"event_type": body["event_type"], "request_id": body["request_id"],
                    "points_delta": 1, "points_after": 4, "duplicate": False}
            if body["event_type"] == "daily_check_in":
                data["request_id"] = "daily-check-in:" + PET + ":2026-10-21"
        else:
            raise AssertionError("Unexpected path")
        return httpx.Response(200, json={"success": True, "data": data})

    @property
    def posts(self):
        return [request for request in self.requests if request.method == "POST"]


def service(tmp_path, device=None, **kwargs):
    options = dict(upstream_url="http://192.168.1.152:8000", api_key=KEY, expected_serial=SERIAL,
                   product_session=TOKEN, product_pet_id=PET, db_path=tmp_path / "private" / "product.sqlite3",
                   interactions_enabled=True, transport=httpx.MockTransport(device or Device()))
    options.update(kwargs)
    return LocalProductService(**options)


async def authorize():
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize("motion_enabled", [True, False])
async def test_tailless_product_views_never_recreate_impossible_unlocks(tmp_path, motion_enabled):
    device = Device()
    device.policy = {"has_tail": False, "motion_enabled": motion_enabled}
    state = raw_intimacy()
    state.update(points=20, level=INTIMACY_LEVELS[1].model_dump(), progress=level_progress(20))
    device.intimacy = state
    device.payload["intimacy"] = deepcopy(state)
    bridge = service(tmp_path, device)
    dashboard = await bridge.read("dashboard")
    assert set(dashboard["unlockedActionIds"]) == {"head_shake", "head_nod", "quiet_companion"}
    assert "基础尾部动作" not in dashboard["intimacy"]["currentUnlocks"]
    intimacy = await bridge.read("intimacy")
    assert "基础尾部动作" not in intimacy["intimacy"]["currentUnlocks"]
    assert "组合动作" not in intimacy["intimacy"]["nextUnlocks"]
    assert not device.posts
    bridge.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["has_tail", "motion_enabled"])
@pytest.mark.parametrize("invalid", [None, "false", 0])
async def test_explicit_malformed_physical_policy_is_not_assumed_legacy_true(tmp_path, field, invalid):
    device = Device()
    device.policy[field] = invalid
    bridge = service(tmp_path, device)
    with pytest.raises(ProductError):
        await bridge.read("dashboard")
    assert not device.posts
    bridge.close()


@pytest.mark.asyncio
async def test_product_read_discards_a_hardware_policy_changed_mid_read(tmp_path):
    class ChangedDevice(Device):
        async def __call__(self, request):
            result = await super().__call__(request)
            if request.url.path.endswith("/intimacy"):
                self.policy["has_tail"] = True
            return result
    device = ChangedDevice()
    device.policy["has_tail"] = False
    bridge = service(tmp_path, device)
    with pytest.raises(ProductError):
        await bridge.read("dashboard")
    assert not device.posts
    bridge.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["dashboard", "intimacy", "growth"])
async def test_product_reads_filter_private_ids_debug_and_never_write_upstream(tmp_path, kind):
    device = Device()
    bridge = service(tmp_path, device)
    result = await bridge.read(kind)
    text = json.dumps(result)
    assert all(secret not in text for secret in [KEY, TOKEN, PET, OWNER, "private_event", "private_revision", "attribute_delta", "debug"])
    assert not device.posts
    assert not any("login" in str(request.url) or "bind" in str(request.url) for request in device.requests)
    if kind == "dashboard":
        assert result["unlockedActionIds"] == ["head_shake", "head_nod"]
        assert result["today"] == {"interactionCount": None, "pointsGained": None}
    if kind == "growth":
        assert result["attributes"][0]["value"] == 0.25
        assert result["recentEvents"][0]["changedAttributes"] == ["empathy", "energy"]
        assert result["behavior"]["directives"] == []
    bridge.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["serial", "owner"])
async def test_owner_and_full_serial_are_rechecked_after_reads(tmp_path, changed):
    device = Device()
    bridge = service(tmp_path, device)
    await bridge.read("growth")
    setattr(device, changed, "other_identity")
    with pytest.raises(ProductError):
        await bridge.read("dashboard")
    assert not device.posts
    bridge.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("event", ["daily_check_in", "touch", "completed_task", "ignored_greeting"])
async def test_four_fixed_interactions_dedupe_and_namespace_upstream_request_id(tmp_path, event):
    device = Device()
    bridge = service(tmp_path, device)
    result = await bridge.interact(user_id="usr_mini", pet_id="pet_mini", event_type=event, request_id="intent1", authorize=authorize)
    assert result["status"] == "recorded"
    assert result["eventType"] == event
    assert await bridge.interact(user_id="usr_mini", pet_id="pet_mini", event_type=event, request_id="intent1", authorize=authorize) == result
    assert len(device.posts) == 1
    body = json.loads(device.posts[0].content)
    assert body["event_type"] == event and body["metadata"] == {}
    assert body["request_id"].startswith("mi_") and len(body["request_id"]) <= 80
    with pytest.raises(ProductError) as error:
        await bridge.interact(user_id="usr_mini", pet_id="pet_mini", event_type="touch" if event != "touch" else "completed_task", request_id="intent1", authorize=authorize)
    assert error.value.code == "INTERACTION_CONFLICT"
    bridge.close()


@pytest.mark.asyncio
async def test_unknown_dispatch_is_never_retried_after_restart_or_with_new_id(tmp_path):
    device = Device()
    async def timeout(_request):
        raise httpx.ReadTimeout("secret upstream details")
    device.post_hook = timeout
    bridge = service(tmp_path, device)
    result = await bridge.interact(user_id="usr_mini", pet_id="pet_mini", event_type="daily_check_in", request_id="intent1", authorize=authorize)
    assert result["status"] == "unknown" and result["pointsAfter"] is None
    bridge.close()
    bridge = service(tmp_path, device)
    again = await bridge.interact(user_id="usr_mini", pet_id="pet_mini", event_type="daily_check_in", request_id="intent1", authorize=authorize)
    assert again["status"] == "unknown" and len(device.posts) == 1
    with pytest.raises(ProductError) as error:
        await bridge.interact(user_id="usr_mini", pet_id="pet_mini", event_type="daily_check_in", request_id="newday", authorize=authorize)
    assert error.value.code == "INTERACTION_REVIEW_REQUIRED"
    assert len(device.posts) == 1
    # A new mini owner cannot bypass an unresolved write belonging to this
    # physical device/product pet, or read the previous owner's receipt.
    with pytest.raises(ProductError) as error:
        await bridge.interact(user_id="usr_reassigned", pet_id="pet_mini", event_type="touch", request_id="intent1", authorize=authorize)
    assert error.value.code == "INTERACTION_REVIEW_REQUIRED"
    assert "intent1" not in str(error.value) and result["receiptId"] not in str(error.value)
    assert len(device.posts) == 1
    bridge.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("unsafe", [True, "false", None, "missing"])
async def test_unverified_interaction_rules_disable_all_writes_but_keep_real_views(tmp_path, unsafe):
    device = Device()
    if unsafe == "missing":
        device.intimacy.pop("debug_unlimited_touch_intimacy")
    else:
        device.intimacy["debug_unlimited_touch_intimacy"] = unsafe
    bridge = service(tmp_path, device)
    for operation in ("dashboard", "intimacy"):
        view = await bridge.read(operation)
        assert view["interactionsEnabled"] is False
        assert view["intimacy"]["points"] == 3
    for event in ("daily_check_in", "touch", "completed_task", "ignored_greeting"):
        with pytest.raises(ProductError) as error:
            await bridge.interact(user_id="usr", pet_id="pet", event_type=event, request_id="test_" + event, authorize=authorize)
        assert error.value.code == "INTERACTION_POLICY_UNVERIFIED"
    assert not device.posts
    bridge.close()


@pytest.mark.asyncio
async def test_interaction_rule_gate_is_rechecked_adjacent_to_dispatch(tmp_path):
    device = Device()
    original = device.__call__
    policy_reads = 0
    async def changed(request):
        nonlocal policy_reads
        if request.url.path.endswith("/intimacy"):
            policy_reads += 1
            if policy_reads == 2:
                device.intimacy["debug_unlimited_touch_intimacy"] = True
        return await original(request)
    bridge = service(tmp_path, transport=httpx.MockTransport(changed))
    with pytest.raises(ProductError) as error:
        await bridge.interact(user_id="usr", pet_id="pet", event_type="touch", request_id="one", authorize=authorize)
    assert error.value.code == "INTERACTION_POLICY_UNVERIFIED"
    assert not device.posts
    assert bridge._find("usr", "pet", "one")["status"] == "failed"
    bridge.close()


@pytest.mark.asyncio
async def test_sending_receipt_recovers_as_unknown_without_dispatch(tmp_path):
    device = Device()
    bridge = service(tmp_path, device)
    with closing(bridge._connect()) as connection, connection:
        connection.execute("INSERT INTO product_receipts(receipt_id,user_id,pet_id,request_id,event_type,status,updated_at) VALUES('receipt_old','usr','pet','one','touch','sending',?)", (TIME,))
    bridge.close()
    bridge = service(tmp_path, device)
    result = await bridge.interact(user_id="usr", pet_id="pet", event_type="touch", request_id="one", authorize=authorize)
    assert result["status"] == "unknown" and not device.requests
    bridge.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("mutate", ["owner", "serial", "authorization"])
async def test_genuine_owner_device_and_mini_authorization_are_adjacent_to_post(tmp_path, mutate):
    device = Device()
    bridge = service(tmp_path, device)
    calls = 0
    async def change():
        nonlocal calls
        calls += 1
        if calls == 2 and mutate != "authorization":
            setattr(device, mutate, "other_identity")
        if calls == 3 and mutate == "authorization":
            raise ProductError(401, "SESSION_EXPIRED", "会话过期")
    with pytest.raises((ProductError, ValueError)):
        await bridge.interact(user_id="usr", pet_id="pet", event_type="touch", request_id="one", authorize=change)
    assert not device.posts
    bridge.close()


@pytest.mark.asyncio
async def test_global_read_and_write_preflight_deadlines_prevent_late_post(tmp_path, monkeypatch):
    device = Device()
    bridge = service(tmp_path, device)
    monkeypatch.setattr(product, "_READ_TIMEOUT", 0.02)
    monkeypatch.setattr(product, "_PREFLIGHT_TIMEOUT", 0.02)
    original = device.__call__
    async def slow(request):
        await asyncio.sleep(0.03)
        return await original(request)
    bridge._transport = httpx.MockTransport(slow)
    with pytest.raises(ProductError):
        await bridge.read("dashboard")
    with pytest.raises(ProductError):
        await bridge.interact(user_id="usr", pet_id="pet", event_type="touch", request_id="one", authorize=authorize)
    await asyncio.sleep(0.04)
    assert not device.posts
    assert bridge._find("usr", "pet", "one") is None
    bridge.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["oversize", "encoding", "nonfinite", "bad_schema", "redirect"])
async def test_invalid_product_read_is_bounded_and_never_exposes_upstream_body(tmp_path, invalid):
    device = Device()
    original = device.__call__
    async def handler(request):
        if request.url.path.endswith("/growth"):
            if invalid == "oversize":
                return httpx.Response(200, content=b"secret" * 30_000)
            if invalid == "encoding":
                return httpx.Response(200, headers={"content-encoding": "gzip"}, content=b"secret")
            if invalid == "nonfinite":
                return httpx.Response(200, content=b'{"success":true,"data":{"value":NaN}}')
            if invalid == "redirect":
                return httpx.Response(302, headers={"location": "https://evil.invalid/private"})
            return httpx.Response(200, json={"success": True, "data": {"secret": TOKEN}})
        return await original(request)
    bridge = service(tmp_path, transport=httpx.MockTransport(handler))
    with pytest.raises(ProductError) as error:
        await bridge.read("growth")
    assert TOKEN not in str(error.value) and not device.posts
    bridge.close()


@pytest.mark.asyncio
async def test_upstream_product_session_only_attaches_to_product_paths(tmp_path):
    device = Device()
    bridge = service(tmp_path, device)
    await bridge.read("growth")
    for request in device.requests:
        if request.url.path.startswith("/api/v1/pets"):
            assert request.headers["X-Mock-Session"] == TOKEN
        else:
            assert "X-Mock-Session" not in request.headers
        assert "Authorization" not in request.headers
    bridge.close()


@pytest.mark.parametrize("unsafe", ["symlink", "hardlink", "world-readable", "wal"])
def test_private_journal_rejects_unsafe_existing_files(tmp_path, unsafe):
    bridge = service(tmp_path)
    path = bridge.path
    bridge.close()
    if unsafe == "symlink":
        copy = path.with_name("original.sqlite3")
        path.rename(copy)
        path.symlink_to(copy)
    elif unsafe == "hardlink":
        os.link(path, path.with_name("other.sqlite3"))
    elif unsafe == "world-readable":
        path.chmod(0o644)
    else:
        path.with_name("product.sqlite3-wal").write_bytes(b"private")
    with pytest.raises(ValueError):
        service(tmp_path)


@pytest.mark.asyncio
async def test_disabled_interactions_keep_real_reads_but_never_post(tmp_path):
    device = Device()
    bridge = service(tmp_path, device, interactions_enabled=False)
    assert (await bridge.read("dashboard"))["interactionsEnabled"] is False
    assert (await bridge.read("intimacy"))["interactionsEnabled"] is False
    with pytest.raises(ProductError) as error:
        await bridge.interact(user_id="usr", pet_id="pet", event_type="touch", request_id="one", authorize=authorize)
    assert error.value.code == "INTERACTIONS_DISABLED" and not device.posts
    bridge.close()


@pytest.mark.asyncio
async def test_closed_service_cannot_use_database_or_device_after_releasing_lock(tmp_path):
    device = Device()
    old = service(tmp_path, device)
    old.close()
    replacement = service(tmp_path, device)
    with pytest.raises(ProductError):
        await old.read("growth")
    with pytest.raises(ProductError):
        await old.interact(user_id="usr", pet_id="pet", event_type="touch", request_id="one", authorize=authorize)
    assert not device.requests
    replacement.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("character", ["\n", "\t", "\x7f", "\x85", "\u202e", "\u2067"])
async def test_product_text_controls_are_rejected_not_silently_rewritten(tmp_path, character):
    device = Device()
    device.payload["personality"]["description"] = "before" + character + "after"
    bridge = service(tmp_path, device)
    with pytest.raises(ProductError):
        await bridge.read("dashboard")
    assert not device.posts
    bridge.close()


@pytest.mark.asyncio
async def test_submission_rechecks_deadline_after_async_client_entry(tmp_path, monkeypatch):
    device = Device()
    bridge = service(tmp_path, device)
    original = httpx.AsyncClient.__aenter__
    entries = 0
    async def delayed(client):
        nonlocal entries
        entries += 1
        # Two identity triplets and two rule checks precede the POST.
        if entries == 9:
            await asyncio.sleep(0.04)
        return await original(client)
    monkeypatch.setattr(httpx.AsyncClient, "__aenter__", delayed)
    monkeypatch.setattr(product, "_PREFLIGHT_TIMEOUT", 0.03)
    with pytest.raises(ValueError):
        await bridge.interact(user_id="usr", pet_id="pet", event_type="touch", request_id="one", authorize=authorize)
    assert not device.posts
    assert bridge._find("usr", "pet", "one")["status"] == "failed"
    bridge.close()


@pytest.mark.asyncio
async def test_journal_commits_before_dispatch_and_late_authorization_prevents_post(tmp_path):
    device = Device()
    bridge = service(tmp_path, device)
    calls = 0
    async def revoke():
        nonlocal calls
        calls += 1
        if calls == 3:
            with closing(bridge._connect()) as connection:
                assert connection.execute("SELECT status FROM product_receipts").fetchone()[0] == "sending"
            raise RuntimeError("revoked")
    with pytest.raises(RuntimeError):
        await bridge.interact(user_id="usr", pet_id="pet", event_type="touch", request_id="one", authorize=revoke)
    assert not device.posts
    assert bridge._find("usr", "pet", "one")["status"] == "failed"
    bridge.close()


@pytest.mark.asyncio
async def test_concurrent_same_request_shares_dispatch_and_other_request_is_busy(tmp_path):
    device = Device()
    started, release = asyncio.Event(), asyncio.Event()
    async def wait(request):
        started.set()
        await release.wait()
        body = json.loads(request.content)
        return httpx.Response(200, json={"success": True, "data": {"event_type": body["event_type"],
            "request_id": body["request_id"], "points_delta": 1, "points_after": 4, "duplicate": False}})
    device.post_hook = wait
    bridge = service(tmp_path, device)
    task = asyncio.create_task(bridge.interact(user_id="usr", pet_id="pet", event_type="touch", request_id="one", authorize=authorize))
    await started.wait()
    duplicate = asyncio.create_task(bridge.interact(user_id="usr", pet_id="pet", event_type="touch", request_id="one", authorize=authorize))
    with pytest.raises(ProductError):
        await bridge.interact(user_id="usr", pet_id="pet", event_type="touch", request_id="two", authorize=authorize)
    release.set()
    assert await task == await duplicate
    assert len(device.posts) == 1
    bridge.close()


def test_private_journal_rejects_other_database_without_altering_it(tmp_path):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    path = directory / "product.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE precious(value TEXT)")
    path.chmod(0o600)
    before = path.read_bytes()
    with pytest.raises(ValueError):
        service(tmp_path)
    assert path.read_bytes() == before


def test_single_service_lock_and_device_fingerprint_persist(tmp_path):
    bridge = service(tmp_path)
    with pytest.raises(ValueError):
        service(tmp_path)
    bridge.close()
    with pytest.raises(ValueError):
        service(tmp_path, expected_serial="other_device")
    bridge = service(tmp_path)
    assert bridge.path.stat().st_mode & 0o777 == 0o600
    bridge.close()


class Provider:
    async def exchange(self, code):
        return code


@pytest.fixture
def gateway(tmp_path):
    store = SessionStore(tmp_path / "sessions.sqlite3")
    bridge = service(tmp_path)
    config = MiniGatewayConfig(db_path=store.path, app_id="wx1234567890abcdef", app_secret="a" * 32, status_pet_id="pet_mini")
    reader = type("Reader", (), {"expected_serial": SERIAL})()
    app = create_app(config, provider=Provider(), store=store, status_reader=reader, product_service=bridge)
    with TestClient(app) as client:
        session = client.post("/v1/auth/wechat", json={"code": "owner"}).json()["data"]
        store.grant_pet(session["userId"], "pet_mini", "小安")
        with store._connect() as connection:
            connection.execute("INSERT INTO mg_meta VALUES(?,?)", ("device:pet_mini", hashlib.sha256(SERIAL.encode()).hexdigest()))
        yield client, bridge, store, session, {"Authorization": "Bearer " + session["accessToken"]}
    bridge.close()


@pytest.mark.parametrize("path", ["dashboard", "intimacy", "growth", "interactions"])
def test_routes_require_session_and_exact_pet_pin(gateway, path):
    client, _, _, _, headers = gateway
    base = "/v1/pets/pet_mini/" + path
    request = client.post if path == "interactions" else client.get
    kwargs = {"json": {"eventType": "touch", "requestId": "one"}} if path == "interactions" else {}
    assert request(base, **kwargs).status_code == 401
    assert request(base.replace("pet_mini", "pet_other"), headers=headers, **kwargs).status_code == 404
    assert request(base, headers=headers, **kwargs).status_code == 200


@pytest.mark.parametrize("extra", [{"eventType": "valid_dialog"}, {"metadata": {}}, {"points": 3}, {"pet_id": PET}, {"requestId": "a/b"}])
def test_interaction_body_accepts_no_raw_metadata_parameters_or_other_event(gateway, extra):
    client, bridge, _, _, headers = gateway
    body = {"eventType": "touch", "requestId": "one", **extra}
    assert client.post("/v1/pets/pet_mini/interactions", headers=headers, json=body).status_code == 400
    assert not bridge._transport.handler.posts


def test_request_bounds_and_read_only_health_consider_interactions(gateway):
    client, bridge, _, _, headers = gateway
    assert client.get("/health").json()["data"]["readOnly"] is False
    bridge.interactions_enabled = False
    assert client.get("/health").json()["data"]["readOnly"] is True
    assert client.post("/v1/pets/pet_mini/interactions", headers=headers, content=b"x" * 1025).status_code == 413
    assert client.get("/v1/pets/pet_mini/growth?token=bad", headers=headers).status_code == 400
    assert client.request("GET", "/v1/pets/pet_mini/growth", headers=headers, content=b"x").status_code == 400
    assert client.post("/v1/pets/pet_mini/interactions", headers=headers,
                       content=b'{"eventType":"touch","eventType":"completed_task","requestId":"one"}').status_code == 400


@pytest.mark.parametrize("operation", ["dashboard", "intimacy", "growth", "interactions"])
@pytest.mark.parametrize("change,expected", [("session", 401), ("owner", 404), ("pin", 503)])
def test_late_product_result_never_escapes_after_mini_authorization_changes(gateway, operation, change, expected):
    client, bridge, store, session, headers = gateway
    original = bridge.read if operation != "interactions" else bridge.interact
    def revoke():
        if change == "session":
            store.revoke_session(session["accessToken"], "wx1234567890abcdef")
        elif change == "owner":
            store.revoke_pet(session["userId"], "pet_mini")
        else:
            with store._connect() as connection:
                connection.execute("DELETE FROM mg_meta WHERE key='device:pet_mini'")
    async def during(*args, **kwargs):
        result = await original(*args, **kwargs)
        revoke()
        return result
    if operation == "interactions":
        bridge.interact = during
        response = client.post("/v1/pets/pet_mini/interactions", headers=headers,
                               json={"eventType": "touch", "requestId": "one"})
    else:
        bridge.read = during
        response = client.get("/v1/pets/pet_mini/" + operation, headers=headers)
    assert response.status_code == expected
    assert "pointsAfter" not in response.text and "attributes" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("event", ["daily_check_in", "touch", "completed_task", "ignored_greeting"])
async def test_bridge_uses_real_product_schema_service_and_repository_without_hardware(client, tmp_path, event, product_app_transport):
    """Only health/device pin fixtures are fake; all product API behavior is real."""
    login = client.post("/api/v1/auth/mock-login", json={"login_code": "explicit-test-owner", "nickname": "测试"}).json()["data"]
    headers = {"X-Mock-Session": login["session_token"]}
    bound = client.post("/api/v1/pets/bind", headers=headers, json={"device_serial": SERIAL, "pet_name": "小安", "network_name": "test"}).json()["data"]
    pet_id = bound["pet"]["pet_id"]
    real = product_app_transport
    device = Device()
    async def handler(request):
        if request.url.path in {"/health", "/api/v1/device/status"}:
            return await device(request)
        return await real.handle_async_request(request)
    bridge = service(tmp_path, product_session=login["session_token"], product_pet_id=pet_id,
                     transport=httpx.MockTransport(handler))
    for operation in ("dashboard", "intimacy", "growth"):
        result = await bridge.read(operation)
        assert result["sampledAt"].endswith("Z")
    receipt = await bridge.interact(user_id="usr_mini", pet_id="pet_mini", event_type=event,
                                    request_id="test_intent", authorize=authorize)
    assert receipt["status"] == "recorded"
    assert receipt["eventType"] == event
    after = client.get("/api/v1/pets/" + pet_id + "/intimacy", headers=headers).json()["data"]
    assert len(after["history"]) == 1 and after["history"][0]["event_type"] == event
    assert await bridge.interact(user_id="usr_mini", pet_id="pet_mini", event_type=event,
                                 request_id="test_intent", authorize=authorize) == receipt
    assert len(client.get("/api/v1/pets/" + pet_id + "/intimacy", headers=headers).json()["data"]["history"]) == 1
    bridge.close()
