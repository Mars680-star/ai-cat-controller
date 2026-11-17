"""Only synthetic transport and private temporary ledgers; no real settings."""

import asyncio
from contextlib import closing
import hashlib
import json
import os
import sqlite3
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from ai_cat_controller.mini_gateway import preferences
from ai_cat_controller.mini_gateway.app import create_app
from ai_cat_controller.mini_gateway.config import MiniGatewayConfig
from ai_cat_controller.mini_gateway.preferences import LocalPreferencesService, PreferencesError
from ai_cat_controller.mini_gateway.preferences_models import Command
from ai_cat_controller.mini_gateway.store import SessionStore

SERIAL, PET, OWNER = "K1-PREFERENCES-SYNTHETIC", "pet_web_test", "usr_web_test"
KEY, WEB = "synthetic_private_device_key", "synthetic_private_web_session"


class Device:
    def __init__(self):
        self.requests, self.write_hook, self.read_hook = [], None, lambda: None
        self.serial, self.owner = SERIAL, OWNER
        self.pet = {"pet_id": PET, "owner_user_id": OWNER, "serial_number": SERIAL,
            "online": True, "name": "小安", "volume": 62, "debug": KEY}
        self.output = {"output_volume_available": True, "output_volume_percent": 62, "output_muted": False}
        self.config = {"follow_up_seconds": 80, "minimum_seconds": 5, "maximum_seconds": 120, "source": "default"}

    async def __call__(self, request):
        self.requests.append(request)
        path = request.url.path
        if request.method != "GET":
            if self.write_hook:
                return await self.write_hook(request)
            return self.ack(request)
        self.read_hook()
        if path == "/health":
            data = {"status": "ok", "adapter": "local_k1"}
        elif path == "/api/v1/device/status":
            data = {"adapter_mode": "local_k1", "connected": True, "device_serial": self.serial, **self.output}
        elif path == "/api/v1/pets":
            data = [{**self.pet, "owner_user_id": self.owner}]
        elif path == "/api/v1/dialog/config":
            data = self.config
        else:
            pytest.fail("Unexpected or unsafe upstream path")
        return httpx.Response(200, json={"success": True, "data": data})

    def ack(self, request):
        body = json.loads(request.content)
        if request.url.path.endswith("/settings"):
            assert request.method == "PATCH"
            self.pet.update(body)
            if "volume" in body:
                self.output.update(output_volume_percent=body["volume"], output_muted=body["volume"] == 0)
            data = self.pet
        elif request.url.path.endswith("/config"):
            assert request.method == "PATCH"
            self.config.update(body, source="persisted")
            data = self.config
        else:
            assert request.method == "POST" and request.url.path.endswith("/feedback")
            data = {"feedback_id": "fb_synthetic", "category": body["category"], "created_at": "2026-10-21T08:00:00Z"}
        return httpx.Response(200, json={"success": True, "data": data})

    @property
    def writes(self):
        return [request for request in self.requests if request.method != "GET"]


def service(tmp_path, device=None, **kwargs):
    values = dict(db_path=tmp_path / "private" / "preferences.sqlite3", settings_enabled=True, feedback_enabled=True,
        upstream_url="http://192.168.1.152:8000", api_key=KEY, expected_serial=SERIAL,
        product_session=WEB, product_pet_id=PET, transport=httpx.MockTransport(device or Device()))
    values.update(kwargs)
    return LocalPreferencesService(**values)


async def authorize():
    pass


def args(kind="settings", request="one", **kwargs):
    values = dict(user_id="usr_mini", pet_id="pet_mini", request_id=request, kind=kind, authorize=authorize)
    values.update(dict(changes={"name": "合成名字", "volume": 35, "followUpSeconds": 25}) if kind == "settings"
                  else dict(category="dialog", content="合成反馈，不保存正文\n第二行\t内容"))
    values.update(kwargs)
    return values


@pytest.mark.asyncio
async def test_real_readings_are_separate_from_saved_preference_and_no_write(tmp_path):
    device = Device()
    device.output["output_volume_percent"] = 125
    client = service(tmp_path, device)
    result = await client.snapshot(user_id="u", pet_id="p")
    assert result["verified"] and result["storedVolume"] == 62 and result["outputVolume"] == 125
    assert result["outputMuted"] is False and result["canChangeVolume"]
    assert result["followUpSeconds"] == 80 and result["followUpSource"] == "default"
    assert not device.writes and set(request.url.path for request in device.requests) == {
        "/health", "/api/v1/pets", "/api/v1/device/status", "/api/v1/dialog/config"}
    assert not any(value in json.dumps(result) for value in (SERIAL, PET, OWNER, KEY, WEB, "debug"))
    client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["serial", "name", "stored", "output", "source", "range"])
async def test_unverified_source_never_produces_defaults_or_enables_writes(tmp_path, bad):
    device = Device()
    if bad == "serial":
        device.serial = "ANOTHER-SYNTHETIC"
    elif bad == "name":
        device.pet["name"] = "名字\u202e"
    elif bad == "stored":
        device.pet["volume"] = True
    elif bad == "output":
        device.output["output_volume_available"] = "true"
    elif bad == "source":
        device.config["source"] = "untrusted"
    else:
        device.config["minimum_seconds"] = 1
    client = service(tmp_path, device)
    result = await client.snapshot(user_id="u", pet_id="p")
    assert not result["verified"] and result["reason"]
    assert all(result[key] is None for key in ("name", "storedVolume", "outputVolume", "outputMuted",
        "outputVolumeAvailable", "followUpSeconds", "followUpSource", "minimumSeconds", "maximumSeconds"))
    assert not any(result[key] for key in ("canChangeName", "canChangeVolume", "canChangeFollowUp", "canSubmitFeedback"))
    with pytest.raises(PreferencesError):
        await client.submit(**args())
    assert not device.writes
    client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("output", [{}, {"output_volume_available": False}, {"output_volume_available": True}])
async def test_missing_output_readings_are_unknown_not_saved_volume(tmp_path, output):
    device = Device()
    device.output = output
    client = service(tmp_path, device)
    result = await client.snapshot(user_id="u", pet_id="p")
    assert result["verified"] and result["outputVolume"] is None and result["outputMuted"] is None
    assert result["outputVolumeAvailable"] is output.get("output_volume_available")
    assert result["canChangeVolume"] is (output.get("output_volume_available") is True)
    if not result["canChangeVolume"]:
        with pytest.raises(PreferencesError) as error:
            await client.submit(**args(changes={"volume": 25}))
        assert error.value.code == "PREFERENCES_VOLUME_UNAVAILABLE"
        assert not device.writes
    assert (await client.submit(**args(changes={"name": "新名字"})))["status"] == "recorded"
    client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["settings", "feedback"])
async def test_fixed_writes_have_durable_steps_and_do_not_store_sensitive_content(tmp_path, kind):
    device = Device()
    client = service(tmp_path, device)
    result = await client.submit(**args(kind))
    assert result["status"] == "recorded" and all(step["status"] == "applied" for step in result["steps"])
    assert await client.submit(**args(kind)) == result
    assert await client.receipt(user_id="usr_mini", pet_id="pet_mini", request_id="one") == result
    assert len(device.writes) == (2 if kind == "settings" else 1)
    assert all(request.headers["X-API-Key"] == KEY for request in device.writes)
    assert all(("X-Mock-Session" in request.headers) == request.url.path.startswith("/api/v1/pets") for request in device.writes)
    with closing(client._connect()) as connection:
        saved = "\n".join(connection.iterdump())
    assert all(value not in saved for value in (KEY, WEB, OWNER, "合成名字", "合成反馈"))
    assert all(value not in json.dumps(result) for value in (KEY, WEB, OWNER, "content", "name", "feedback_id"))
    with pytest.raises(PreferencesError) as error:
        await client.submit(**args(kind, **({"changes": {"name": "另一名字"}} if kind == "settings" else {"content": "另一反馈"})))
    assert error.value.code == "PREFERENCES_CONFLICT"
    with pytest.raises(PreferencesError) as error:
        await client.receipt(user_id="another", pet_id="pet_mini", request_id="one")
    assert error.value.status_code == 404
    client.close()


@pytest.mark.asyncio
async def test_noop_and_only_changed_fields_do_not_patch_unrelated_values(tmp_path):
    device = Device()
    client = service(tmp_path, device)
    result = await client.submit(**args(changes={"name": "小安", "volume": 62, "followUpSeconds": 80}))
    assert result["status"] == "not_sent" and not device.writes
    result = await client.submit(**args(request="second", changes={"name": "小安", "volume": 35, "followUpSeconds": 80}))
    assert result["status"] == "recorded" and len(device.writes) == 1
    assert json.loads(device.writes[0].content) == {"volume": 35}
    assert [step["status"] for step in result["steps"]] == ["applied", "not_sent"]
    client.close()


@pytest.mark.asyncio
async def test_user_can_correct_real_volume_or_mute_when_stored_value_matches(tmp_path):
    device = Device()
    device.output.update(output_volume_percent=120, output_muted=True)
    client = service(tmp_path, device)
    assert (await client.submit(**args(changes={"volume": 62})))["status"] == "recorded"
    assert json.loads(device.writes[0].content) == {"volume": 62}
    client.close()


@pytest.mark.asyncio
async def test_known_second_step_not_sent_is_partial_and_allows_fresh_explicit_correction(tmp_path):
    device = Device()
    client = service(tmp_path, device)
    original = client._dispatch
    async def dispatch(key, command, step, authorize, deadline, dispatched):
        if step == "follow_up":
            raise PreferencesError()
        return await original(key, command, step, authorize, deadline, dispatched)
    client._dispatch = dispatch
    result = await client.submit(**args())
    assert result["status"] == "partially_applied" and [step["status"] for step in result["steps"]] == ["applied", "not_sent"]
    assert len(device.writes) == 1 and not (await client.snapshot(user_id="u", pet_id="p"))["settingsReviewRequired"]
    assert await client.submit(**args()) == result and len(device.writes) == 1
    client._dispatch = original
    result = await client.submit(**args(request="fresh", changes={"followUpSeconds": 25}))
    assert result["status"] == "recorded" and len(device.writes) == 2
    client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,bad", [(kind, bad) for kind in ("settings", "feedback") for bad in
    ("timeout", "status", "extra_or_mismatch", "oversize", "compressed", "duplicate")])
async def test_unknown_latches_only_corresponding_kind_and_never_retries_even_after_restart(tmp_path, kind, bad):
    device = Device()
    async def response(request):
        if bad == "timeout":
            raise httpx.ReadTimeout("Private " + KEY)
        if bad == "status":
            return httpx.Response(500)
        if bad == "oversize":
            return httpx.Response(200, content=b"x" * (preferences._MAX_BYTES + 1))
        if bad == "compressed":
            return httpx.Response(200, content=b"not gzip", headers={"Content-Encoding": "unsafe"})
        if bad == "duplicate":
            return httpx.Response(200, content=b'{"success":true,"success":true,"data":{}}')
        value = device.ack(request).json()
        value["data"]["private" if kind == "feedback" else "owner_user_id"] = KEY
        return httpx.Response(200, json=value)
    device.write_hook = response
    client = service(tmp_path, device)
    result = await client.submit(**args(kind))
    assert result["status"] == "unknown" and result["steps"][0]["status"] == "unknown"
    assert all(step["status"] == "not_sent" for step in result["steps"][1:])
    client.close()
    client = service(tmp_path, device)
    assert (await client.submit(**args(kind)))["status"] == "unknown" and len(device.writes) == 1
    for user in ("usr_mini", "another_mini"):
        with pytest.raises(PreferencesError) as error:
            await client.submit(**{**args(kind, "fresh"), "user_id": user})
        assert error.value.code == "PREFERENCES_REVIEW_REQUIRED"
    device.write_hook = None
    other = "feedback" if kind == "settings" else "settings"
    assert (await client.submit(**args(other, "independent")))["status"] == "recorded"
    snapshot = await client.snapshot(user_id="u", pet_id="p")
    assert snapshot[kind + "ReviewRequired"] and not snapshot[other + "ReviewRequired"]
    assert snapshot["manualReviewRequired"]
    client.close()


@pytest.mark.asyncio
async def test_second_step_unknown_keeps_first_applied_and_never_replays_it(tmp_path):
    device = Device()
    async def response(request):
        if request.url.path.endswith("/config"):
            raise httpx.ReadTimeout("synthetic lost ACK")
        return device.ack(request)
    device.write_hook = response
    client = service(tmp_path, device)
    result = await client.submit(**args())
    assert result["status"] == "unknown" and [step["status"] for step in result["steps"]] == ["applied", "unknown"]
    assert await client.submit(**args()) == result and len(device.writes) == 2
    client.close()


@pytest.mark.asyncio
async def test_valid_slow_first_ack_does_not_consume_next_step_preflight_budget(tmp_path, monkeypatch):
    device = Device()
    client = service(tmp_path, device)
    clock = [100.0]
    monkeypatch.setattr(preferences, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    async def response(request):
        if request.url.path.endswith("/settings"):
            clock[0] += 4.0
        return device.ack(request)
    device.write_hook = response
    result = await client.submit(**args())
    assert result["status"] == "recorded" and len(device.writes) == 2
    assert [step["status"] for step in result["steps"]] == ["applied", "applied"]
    client.close()


@pytest.mark.asyncio
async def test_cancelled_caller_cannot_duplicate_pending_write_and_other_kind_stays_available(tmp_path):
    device = Device()
    client = service(tmp_path, device)
    started, release = asyncio.Event(), asyncio.Event()
    async def deferred(request):
        if request.url.path.endswith("/settings"):
            row = client._receipt(("usr_mini", "pet_mini", "one"))
            assert row["status"] == "sending" and row["steps"][0]["status"] == "sending"
            started.set()
            await release.wait()
        return device.ack(request)
    device.write_hook = deferred
    first = asyncio.create_task(client.submit(**args(changes={"name": "合成名字"})))
    await started.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert (await client.submit(**args(changes={"name": "合成名字"})))["status"] == "sending"
    with pytest.raises(PreferencesError) as error:
        await client.submit(**args(request="different"))
    assert error.value.code == "PREFERENCES_BUSY"
    assert (await client.submit(**args("feedback", "other")))["status"] == "recorded"
    with pytest.raises(RuntimeError):
        client.close()
    release.set()
    await asyncio.gather(*list(client._tasks.values()))
    assert len(device.writes) == 2
    client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["serial", "owner", "config", "volume", "disabled"])
async def test_client_entry_revalidates_identity_range_and_capability_before_dispatch(tmp_path, monkeypatch, change):
    device = Device()
    client = service(tmp_path, device)
    original = httpx.AsyncClient
    count = [0]
    class Client(original):
        async def __aenter__(self):
            count[0] += 1
            # Initial probe is seven GET clients. Eighth is the writing client.
            if count[0] == 8:
                if change == "serial":
                    device.serial = "OTHER-SYNTHETIC"
                elif change == "owner":
                    device.owner = "usr_other"
                elif change == "config":
                    device.config["maximum_seconds"] = 999
                elif change == "volume":
                    device.output["output_volume_available"] = False
                else:
                    client.settings_enabled = False
            return await super().__aenter__()
    monkeypatch.setattr(preferences.httpx, "AsyncClient", Client)
    result = await client.submit(**args())
    assert result["status"] == "not_sent" and not device.writes
    assert not client._review("settings")
    client.close()


@pytest.mark.asyncio
async def test_preflight_deadline_prevents_any_late_dispatch(tmp_path, monkeypatch):
    device = Device()
    client = service(tmp_path, device)
    monkeypatch.setattr(preferences, "_PREFLIGHT_TIMEOUT", .01)
    async def slow_auth():
        await asyncio.sleep(.03)
    with pytest.raises(PreferencesError):
        await client.submit(**args(authorize=slow_auth))
    assert not device.writes and client._find(("usr_mini", "pet_mini", "one")) is None
    client.close()


@pytest.mark.asyncio
async def test_disk_failure_after_delivery_blocks_until_restart_then_unknown(tmp_path, monkeypatch):
    device = Device()
    client = service(tmp_path, device)
    original = client._step
    def fail(key, kind, step, state, **kwargs):
        if state in {"applied", "unknown"}:
            raise sqlite3.OperationalError("synthetic disk full")
        return original(key, kind, step, state, **kwargs)
    monkeypatch.setattr(client, "_step", fail)
    with pytest.raises(sqlite3.OperationalError):
        await client.submit(**args(changes={"name": "合成名字"}))
    assert len(device.writes) == 1
    assert (await client.snapshot(user_id="u", pet_id="p"))["settingsReviewRequired"]
    with pytest.raises(PreferencesError):
        await client.submit(**args(request="new"))
    client.close()
    client = service(tmp_path, device)
    assert (await client.receipt(user_id="usr_mini", pet_id="pet_mini", request_id="one"))["status"] == "unknown"
    assert client._review("settings") and not client._review("feedback")
    client.close()


def test_private_store_is_exclusive_pinned_and_never_initializes_unrelated_database(tmp_path):
    client = service(tmp_path)
    with pytest.raises(ValueError):
        service(tmp_path)
    path = client.path
    client.close()
    before = path.read_bytes()
    with pytest.raises(ValueError):
        service(tmp_path, expected_serial="ANOTHER-SYNTHETIC")
    assert path.read_bytes() == before
    other = tmp_path / "precious.sqlite3"
    with sqlite3.connect(other) as connection:
        connection.execute("CREATE TABLE precious(value TEXT)")
    other.chmod(0o600)
    before = other.read_bytes()
    with pytest.raises(ValueError):
        service(tmp_path, db_path=other)
    assert other.read_bytes() == before


@pytest.mark.parametrize("unsafe", ["symlink", "hardlink", "public", "wal", "trigger", "schema", "package"])
def test_unsafe_private_store_rejected_without_mutation(tmp_path, unsafe):
    client = service(tmp_path)
    path = client.path
    client.close()
    if unsafe == "symlink":
        old = path.with_name("original.sqlite3")
        path.rename(old)
        path.symlink_to(old)
    elif unsafe == "hardlink":
        os.link(path, path.with_name("alias.sqlite3"))
    elif unsafe == "public":
        path.chmod(0o644)
    elif unsafe == "wal":
        path.with_name(path.name + "-wal").write_bytes(b"private")
    elif unsafe == "package":
        path = tmp_path / "miniprogram" / "ledger.sqlite3"
    else:
        with sqlite3.connect(path) as connection:
            if unsafe == "trigger":
                connection.execute("CREATE TRIGGER evil AFTER INSERT ON pf_meta BEGIN DELETE FROM pf_requests; END")
            else:
                connection.execute("ALTER TABLE pf_requests ADD COLUMN extra TEXT")
    before = path.read_bytes() if path.exists() else None
    with pytest.raises(ValueError):
        service(tmp_path, db_path=path)
    assert (path.read_bytes() if path.exists() else None) == before


@pytest.mark.asyncio
async def test_disabled_flags_independent_and_closed_read_cannot_mutate_pin(tmp_path):
    device = Device()
    client = service(tmp_path, device, settings_enabled=False)
    result = await client.snapshot(user_id="u", pet_id="p")
    assert result["verified"] and not result["canChangeName"] and result["canSubmitFeedback"]
    with pytest.raises(PreferencesError) as error:
        await client.submit(**args())
    assert error.value.status_code == 403
    client.close()
    before, count = client.path.read_bytes(), len(device.requests)
    with pytest.raises(PreferencesError):
        await client.snapshot(user_id="u", pet_id="p")
    assert client.path.read_bytes() == before and len(device.requests) == count


@pytest.mark.parametrize("changes", [{}, {"name": None}, {"volume": True}, {"volume": 101}, {"volume": -1},
    {"followUpSeconds": 4}, {"followUpSeconds": 121}, {"followUpSeconds": "30"}, {"name": " "},
    {"name": "x" * 21}, {"name": "名字\n"}, {"name": "名字\u202e"}, {"speed": 100}])
def test_settings_contract_rejects_nonfixed_and_invalid_fields(changes):
    with pytest.raises(ValueError):
        Command.model_validate({"requestId": "one", "kind": "settings", "changes": changes})


@pytest.mark.parametrize("text", ["x", " " * 10, "x" * 501, "内容\x00", "内容\r", "内容\x7f", "内容\u202e", "内容\ud800"])
def test_feedback_contract_rejects_unsafe_text_without_cleaning(text):
    with pytest.raises(ValueError):
        Command.model_validate({"requestId": "one", "kind": "feedback", "category": "other", "content": text})


class Provider:
    async def exchange(self, code):
        return code


@pytest.fixture
def gateway(tmp_path):
    device = Device()
    control = service(tmp_path, device)
    store = SessionStore(tmp_path / "sessions.sqlite3")
    config = MiniGatewayConfig(db_path=store.path, app_id="wx1234567890abcdef", app_secret="a" * 32, status_pet_id="pet_mini")
    status = type("Status", (), {"expected_serial": SERIAL})()
    app = create_app(config, provider=Provider(), store=store, status_reader=status, preferences_service=control)
    with TestClient(app) as client:
        identity = client.post("/v1/auth/wechat", json={"code": "owner"}).json()["data"]
        store.grant_pet(identity["userId"], "pet_mini", "小安")
        with store._connect() as connection:
            connection.execute("INSERT INTO mg_meta VALUES(?,?)", ("device:pet_mini", hashlib.sha256(SERIAL.encode()).hexdigest()))
        yield client, control, device, store, identity, {"Authorization": "Bearer " + identity["accessToken"]}
    control.close()


def test_routes_require_auth_only_new_commands_get_4096_bytes_and_cache_control(gateway):
    client, _, device, _, _, headers = gateway
    base = "/v1/pets/pet_mini/preferences"
    assert client.get(base).status_code == 401
    assert client.get(base + "?token=bad", headers=headers).status_code == 400
    assert client.request("GET", base, headers=headers, content=b"x").status_code == 400
    assert client.post(base + "/commands", headers=headers, content=b"x" * 4097).status_code == 413
    assert client.post("/v1/auth/wechat", content=b"x" * 1025).status_code == 413
    raw = json.dumps({"requestId": "one", "kind": "feedback", "category": "other", "content": "汉" * 500})
    response = client.post(base + "/commands", headers=headers, content=raw)
    assert response.status_code == 200 and response.json()["data"]["status"] == "recorded"
    assert len(device.writes) == 1
    assert response.headers["cache-control"] == "no-store" and response.headers["x-content-type-options"] == "nosniff"
    assert client.get(base + "/requests/one", headers=headers).json()["data"] == response.json()["data"]
    assert client.get(base + "/requests/unknown", headers=headers).status_code == 404


def test_preferences_optin_contributes_to_health_readonly_truthfully(gateway):
    client, control, _, _, _, _ = gateway
    assert client.get("/health").json()["data"]["readOnly"] is False
    control.settings_enabled = control.feedback_enabled = False
    assert client.get("/health").json()["data"]["readOnly"] is True


@pytest.mark.parametrize("body", [{"requestId": "one", "kind": "reset"},
    {"requestId": "one", "kind": "settings", "changes": {"name": "名字"}, "content": None},
    {"requestId": "one", "kind": "feedback", "category": "other", "content": "反馈", "changes": None},
    {"requestId": "one", "kind": "feedback", "category": "unsafe", "content": "反馈"},
    {"requestId": "one", "kind": "settings", "changes": {"url": "unsafe"}}])
def test_routes_reject_extra_or_dangerous_fields(gateway, body):
    client, _, device, _, _, headers = gateway
    assert client.post("/v1/pets/pet_mini/preferences/commands", headers=headers, json=body).status_code == 400
    assert not device.writes


@pytest.mark.parametrize("change,expected", [("session", 401), ("pet", 404), ("pin", 503)])
def test_late_mini_authorization_is_rechecked_before_patch(gateway, change, expected):
    client, _, device, store, identity, headers = gateway
    def revoke():
        if change == "session":
            store.revoke_session(identity["accessToken"], "wx1234567890abcdef")
        elif change == "pet":
            store.revoke_pet(identity["userId"], "pet_mini")
        else:
            with store._connect() as connection:
                connection.execute("DELETE FROM mg_meta WHERE key='device:pet_mini'")
    device.read_hook = revoke
    result = client.post("/v1/pets/pet_mini/preferences/commands", headers=headers,
        json={"requestId": "one", "kind": "settings", "changes": {"name": "新名字"}})
    assert result.status_code == expected and not device.writes
