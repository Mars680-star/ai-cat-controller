"""Authenticated fixed-motion routes, using only fake controllers and temp DBs."""

from copy import deepcopy
import hashlib
import logging

from fastapi.testclient import TestClient
import pytest

from ai_cat_controller.mini_gateway.app import create_app
from ai_cat_controller.mini_gateway.config import MiniGatewayConfig
from ai_cat_controller.mini_gateway.motion import MotionDisabled
from ai_cat_controller.mini_gateway.store import SessionStore

APP_ID = "wx1234567890abcdef"
SERIAL = "7c2b63fd4a128"
PET = "pet_one"
BASE = "/v1/pets/" + PET + "/motion"


class Provider:
    async def exchange(self, code):
        return code


class Reader:
    expected_serial = SERIAL


def snapshot(enabled=False):
    return {"enabled": enabled, "verified": True, "deviceBusy": False,
            "actions": [{"actionId": action, "name": name, "available": enabled, "reason": "" if enabled else "等待现场确认"}
                        for action, name in (("head_nod", "点头"), ("head_shake", "摇头"),
                                             ("tail_wag", "开心摇尾"), ("proud_pose", "骄傲转身"),
                                             ("quiet_companion", "安静陪伴"), ("greeting_combo", "见面问候"),
                                             ("celebration_combo", "升级庆祝"))],
            "execution": None, "sampledAt": "2026-10-20T08:00:00Z"}


class Controller:
    expected_serial = SERIAL
    enabled = False

    def __init__(self):
        self.calls = []
        self.posts = []
        self.during = lambda: None
        self.result = None
        self.history_result = None

    async def snapshot(self, **kwargs):
        self.calls.append(("read", kwargs))
        self.during()
        return deepcopy(self.result or snapshot(self.enabled))

    async def history(self, **kwargs):
        self.calls.append(("history", kwargs))
        self.during()
        return deepcopy(self.history_result if self.history_result is not None else {"items": [], "limit": 20})

    async def command(self, name, kwargs):
        self.calls.append((name, kwargs))
        if not self.enabled:
            raise MotionDisabled()
        self.during()
        await kwargs["authorize"]()
        self.posts.append(name)
        return deepcopy(self.result or snapshot(self.enabled))

    async def execute(self, **kwargs):
        return await self.command("execute", kwargs)

    async def stop(self, **kwargs):
        return await self.command("stop", kwargs)


@pytest.fixture
def bound(tmp_path):
    store = SessionStore(tmp_path / "sessions.sqlite3")
    controller = Controller()
    config = MiniGatewayConfig(db_path=store.path, app_id=APP_ID, app_secret="a" * 32, status_pet_id=PET)
    app = create_app(config, provider=Provider(), store=store, status_reader=Reader(), motion_controller=controller)
    with TestClient(app) as client:
        session = client.post("/v1/auth/wechat", json={"code": "owner"}).json()["data"]
        headers = {"Authorization": "Bearer " + session["accessToken"]}
        store.grant_pet(session["userId"], PET, "小安")
        with store._connect() as connection:
            connection.execute("INSERT INTO mg_meta VALUES(?,?)", ("device:" + PET, hashlib.sha256(SERIAL.encode()).hexdigest()))
        yield client, store, controller, headers, session


def command(client, suffix, headers, **kwargs):
    body = {"requestId": "request_one"}
    if suffix == "execute":
        body["actionId"] = "head_nod"
    body.update(kwargs)
    return client.post(BASE + "/" + suffix, headers=headers, json=body)


@pytest.mark.parametrize("suffix", ["read", "execute", "stop"])
def test_all_motion_routes_require_real_business_session(bound, suffix):
    client, _, controller, _, _ = bound
    response = client.get(BASE) if suffix == "read" else command(client, suffix, {})
    assert response.status_code == 401
    assert not controller.calls


def test_other_owner_and_unknown_pet_do_not_reach_controller(bound):
    client, _, controller, headers, _ = bound
    other = client.post("/v1/auth/wechat", json={"code": "other"}).json()["data"]
    response = client.get(BASE, headers={"Authorization": "Bearer " + other["accessToken"]})
    assert response.status_code == 404
    assert client.get("/v1/pets/unknown/motion", headers=headers).status_code == 404
    assert not controller.calls


def test_disabled_catalog_is_readable_but_never_claims_available(bound):
    client, _, controller, headers, session = bound
    response = client.get(BASE, headers=headers)
    assert response.status_code == 200
    assert response.json()["data"] == snapshot(False)
    assert controller.calls == [("read", {"user_id": session["userId"], "pet_id": PET})]
    assert not controller.posts
    assert client.get("/health").json()["data"]["readOnly"] is True


def test_verified_no_tail_exact_three_catalog_passes_with_pause_metadata(bound):
    client, _, controller, headers, _ = bound
    sample = snapshot(False)
    sample.update(hasTail=False, deviceMotionEnabled=False)
    sample["actions"] = [item for item in sample["actions"] if item["actionId"] in {"head_nod", "head_shake", "quiet_companion"}]
    controller.result = sample
    response = client.get(BASE, headers=headers)
    assert response.status_code == 200 and response.json()["data"] == sample


@pytest.mark.parametrize("change", [
    {"hasTail": True}, {"hasTail": None}, {}, {"hasTail": "false"}, {"hasTail": 0},
    {"hasTail": False, "verified": False}, {"hasTail": False, "deviceMotionEnabled": 0},
])
def test_short_catalog_is_rejected_without_verified_explicit_false(bound, change):
    client, _, controller, headers, _ = bound
    sample = snapshot(False)
    sample["actions"] = [item for item in sample["actions"] if item["actionId"] in {"head_nod", "head_shake", "quiet_companion"}]
    sample.update(change)
    controller.result = sample
    assert client.get(BASE, headers=headers).status_code == 503
    assert not controller.posts


@pytest.mark.parametrize("change", [{"hasTail": False}, {"deviceMotionEnabled": False}])
def test_full_catalog_cannot_contradict_no_tail_or_paused_available_actions(bound, change):
    client, _, controller, headers, _ = bound
    controller.result = {**snapshot(True), **change}
    assert client.get(BASE, headers=headers).status_code == 503


@pytest.mark.parametrize("suffix", ["execute", "stop"])
def test_disabled_gate_rejects_both_writes(bound, suffix):
    client, _, controller, headers, _ = bound
    response = command(client, suffix, headers)
    assert response.status_code == 403
    assert response.json()["code"] == "MOTION_DISABLED"
    assert not controller.posts


@pytest.mark.parametrize("extra", [{"intensity": 1}, {"duration_ms": 900}, {"url": "http://example.com"},
                                   {"actionId": "jump"}, {"requestId": ""}, {"requestId": "bad/id"}])
def test_body_never_accepts_hardware_parameters_arbitrary_targets_or_bad_ids(bound, extra):
    client, _, controller, headers, _ = bound
    assert command(client, "execute", headers, **extra).status_code == 400
    assert not controller.calls


def test_stop_rejects_unknown_target_shape(bound):
    client, _, controller, headers, _ = bound
    assert command(client, "stop", headers, executionId="bad/id").status_code == 400
    assert command(client, "stop", headers, actionId="head_nod").status_code == 400
    assert not controller.calls


def test_small_body_bound_and_query_guard_are_preserved(bound):
    client, _, controller, headers, _ = bound
    assert client.post(BASE + "/execute", headers=headers, content=b"x" * 1025).status_code == 413
    assert client.get(BASE + "?token=unsafe", headers=headers).status_code == 400
    assert not controller.calls


def test_identity_pin_and_status_route_must_match_before_read(bound):
    client, store, controller, headers, _ = bound
    controller.expected_serial = "7c2OTHERSa128"
    assert client.get(BASE, headers=headers).status_code == 503
    controller.expected_serial = SERIAL
    with store._connect() as connection:
        connection.execute("DELETE FROM mg_meta WHERE key=?", ("device:" + PET,))
    assert client.get(BASE, headers=headers).status_code == 503
    assert not controller.calls


@pytest.mark.parametrize("suffix", ["execute", "stop"])
def test_authorization_is_rechecked_immediately_before_both_posts(bound, suffix):
    client, store, controller, headers, session = bound
    controller.enabled = True
    controller.during = lambda: store.revoke_session(session["accessToken"], APP_ID)
    assert command(client, suffix, headers).status_code == 401
    assert not controller.posts


def test_session_revoked_during_pin_lookup_is_rejected_before_controller(bound, monkeypatch):
    client, store, controller, headers, session = bound
    controller.enabled = True
    original = store.registered_device_matches

    def lookup(*args):
        result = original(*args)
        store.revoke_session(session["accessToken"], APP_ID)
        return result

    monkeypatch.setattr(store, "registered_device_matches", lookup)
    assert command(client, "execute", headers).status_code == 401
    assert not controller.calls


@pytest.mark.parametrize("change, expected", [("owner", 404), ("session", 401), ("pin", 503)])
def test_late_snapshot_does_not_escape_after_authorization_changes(bound, change, expected):
    client, store, controller, headers, session = bound

    def during():
        if change == "owner":
            store.revoke_pet(session["userId"], PET)
        elif change == "session":
            store.revoke_session(session["accessToken"], APP_ID)
        else:
            with store._connect() as connection:
                connection.execute("DELETE FROM mg_meta WHERE key=?", ("device:" + PET,))

    controller.during = during
    response = client.get(BASE, headers=headers)
    assert response.status_code == expected
    assert "actions" not in response.text


def test_enabled_test_controller_reports_health_truthfully_and_only_fixed_payloads(bound):
    client, _, controller, headers, _ = bound
    controller.enabled = True
    assert client.get("/health").json()["data"]["readOnly"] is False
    assert command(client, "execute", headers).status_code == 200
    kwargs = controller.calls[-1][1]
    assert kwargs["action_id"] == "head_nod"
    assert kwargs["request_id"] == "request_one"
    assert not {"intensity", "duration_ms", "url", "api_key"} & kwargs.keys()
    assert controller.posts == ["execute"]


@pytest.mark.parametrize("action", ["tail_wag", "proud_pose", "quiet_companion", "greeting_combo", "celebration_combo"])
def test_product_actions_use_same_guarded_fixed_execute_route(bound, action):
    client, _, controller, headers, _ = bound
    controller.enabled = True
    response = command(client, "execute", headers, actionId=action)
    assert response.status_code == 200
    assert controller.calls[-1][1]["action_id"] == action
    assert len(response.json()["data"]["actions"]) == 7


@pytest.mark.parametrize("shape", ["missing", "duplicate", "unknown"])
def test_catalog_must_contain_exactly_all_seven_fixed_actions(bound, shape):
    client, _, controller, headers, _ = bound
    controller.result = snapshot(False)
    if shape == "missing":
        controller.result["actions"].pop()
    elif shape == "duplicate":
        controller.result["actions"][-1] = deepcopy(controller.result["actions"][0])
    else:
        controller.result["actions"][-1]["actionId"] = "arbitrary_preset"
    assert client.get(BASE, headers=headers).status_code == 503


@pytest.mark.parametrize("bad", ["privateField", "availableWhileDisabled", "invalidTime"])
def test_controller_output_is_validated_and_does_not_leak_unknown_fields(bound, bad):
    client, _, controller, headers, _ = bound
    controller.result = snapshot(False)
    if bad == "privateField":
        controller.result["privateField"] = "never-return-private-value"
    elif bad == "availableWhileDisabled":
        controller.result["actions"][0]["available"] = True
    else:
        controller.result["sampledAt"] = "2026-02-30T12:00:00Z"
    response = client.get(BASE, headers=headers)
    assert response.status_code == 503
    assert "never-return-private-value" not in response.text


def test_missing_controller_does_not_create_a_fake_available_catalog(tmp_path):
    store = SessionStore(tmp_path / "sessions.sqlite3")
    config = MiniGatewayConfig(db_path=store.path, app_id=APP_ID, app_secret="a" * 32, status_pet_id=PET)
    with TestClient(create_app(config, provider=Provider(), store=store, status_reader=Reader())) as client:
        data = client.post("/v1/auth/wechat", json={"code": "owner"}).json()["data"]
        store.grant_pet(data["userId"], PET, "小安")
        assert client.get(BASE, headers={"Authorization": "Bearer " + data["accessToken"]}).status_code == 503


@pytest.mark.parametrize("enabled, expected", [(False, 403), (True, 200)])
def test_motion_diagnostic_logs_only_fixed_operation_and_status(bound, caplog, enabled, expected):
    client, _, controller, headers, session = bound
    controller.enabled = enabled
    with caplog.at_level(logging.WARNING, logger="ai_cat_controller.mini_gateway.app"):
        assert command(client, "execute", headers, requestId="private-request-value").status_code == expected
        assert command(client, "stop", {}).status_code == 401
    messages = [record.getMessage() for record in caplog.records
                if record.name == "ai_cat_controller.mini_gateway.app"]
    assert messages == ([f"mini motion rejected: operation=execute status={expected}"]
                        if expected >= 400 else []) + ["mini motion rejected: operation=stop status=401"]
    for private in (session["accessToken"], session["userId"], PET, SERIAL, "private-request-value"):
        assert all(private not in message for message in messages)


def history_item(index=0):
    return {"executionId": f"mx_entry_{index}", "actionId": "stop" if index % 2 else "head_nod",
            "status": "stopped" if index % 2 else "ended_unverified",
            "requestId": f"entry_{index}", "updatedAt": "2026-10-20T08:00:00Z"}


def test_history_requires_current_business_session(bound):
    client, _, controller, _, _ = bound
    assert client.get(BASE + "/history").status_code == 401
    assert not controller.calls


def test_history_other_owner_and_unknown_pet_do_not_reach_controller(bound):
    client, _, controller, headers, _ = bound
    other = client.post("/v1/auth/wechat", json={"code": "history_other"}).json()["data"]
    assert client.get(BASE + "/history", headers={"Authorization": "Bearer " + other["accessToken"]}).status_code == 404
    assert client.get("/v1/pets/unknown/motion/history", headers=headers).status_code == 404
    assert not controller.calls


def test_history_default_disabled_still_reads_empty_authorized_history(bound):
    client, _, controller, headers, session = bound
    response = client.get(BASE + "/history", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"success": True, "data": {"items": [], "limit": 20}}
    assert response.headers["Cache-Control"] == "no-store"
    assert controller.calls == [("history", {"user_id": session["userId"], "pet_id": PET})]
    assert not controller.posts


def test_history_preserves_ledger_order_and_stop_receipts(bound):
    client, _, controller, headers, _ = bound
    controller.history_result = {"items": [history_item(index) for index in range(19, -1, -1)], "limit": 20}
    response = client.get(BASE + "/history", headers=headers)
    assert response.status_code == 200
    assert response.json()["data"] == controller.history_result
    assert not controller.posts


@pytest.mark.parametrize("action", ["tail_wag", "proud_pose", "quiet_companion", "greeting_combo", "celebration_combo"])
def test_history_accepts_only_canonical_new_product_action_ids(bound, action):
    client, _, controller, headers, _ = bound
    item = history_item()
    item["actionId"] = action
    controller.history_result = {"items": [item], "limit": 20}
    response = client.get(BASE + "/history", headers=headers)
    assert response.status_code == 200
    assert response.json()["data"]["items"][0]["actionId"] == action


@pytest.mark.parametrize("change,expected", [("owner", 404), ("session", 401), ("pin", 503)])
def test_history_response_is_withheld_after_late_authorization_change(bound, change, expected):
    client, store, controller, headers, session = bound
    controller.history_result = {"items": [history_item()], "limit": 20}

    def during():
        if change == "owner":
            store.revoke_pet(session["userId"], PET)
        elif change == "session":
            store.revoke_session(session["accessToken"], APP_ID)
        else:
            with store._connect() as connection:
                connection.execute("DELETE FROM mg_meta WHERE key=?", ("device:" + PET,))

    controller.during = during
    response = client.get(BASE + "/history", headers=headers)
    assert response.status_code == expected
    assert "executionId" not in response.text and "items" not in response.json()
    assert not controller.posts


def test_history_checks_matching_full_pin_before_controller(bound, monkeypatch):
    client, store, controller, headers, session = bound
    controller.expected_serial = "7c2OTHERSa128"
    assert client.get(BASE + "/history", headers=headers).status_code == 503
    controller.expected_serial = SERIAL
    original = store.registered_device_matches

    def lookup(*args):
        result = original(*args)
        store.revoke_session(session["accessToken"], APP_ID)
        return result

    monkeypatch.setattr(store, "registered_device_matches", lookup)
    assert client.get(BASE + "/history", headers=headers).status_code == 401
    assert not controller.calls


def test_history_has_no_query_body_limit_or_write_entry(bound):
    client, _, controller, headers, _ = bound
    for query in ("?limit=1", "?token=unsafe", "?offset=20"):
        assert client.get(BASE + "/history" + query, headers=headers).status_code == 400
    assert client.request("GET", BASE + "/history", headers=headers, content=b"{}").status_code == 400
    assert client.post(BASE + "/history", headers=headers).status_code == 405
    assert not controller.calls


@pytest.mark.parametrize("bad", ["extraField", "extraItemField", "oversized", "badLimit", "stringLimit",
                                "badTime", "longTime", "badStatus", "badAction", "badId", "duplicate", "missing", "notList"])
def test_history_rejects_unbounded_or_untrusted_controller_output(bound, bad):
    client, _, controller, headers, _ = bound
    value = {"items": [history_item()], "limit": 20}
    if bad == "extraField":
        value["private"] = "never-return-private-value"
    elif bad == "extraItemField":
        value["items"][0]["private"] = "never-return-private-value"
    elif bad == "oversized":
        value["items"] = [history_item(index) for index in range(21)]
    elif bad == "badLimit":
        value["limit"] = 21
    elif bad == "stringLimit":
        value["limit"] = "20"
    elif bad == "badTime":
        value["items"][0]["updatedAt"] = "2026-02-30T08:00:00Z"
    elif bad == "longTime":
        value["items"][0]["updatedAt"] = "2026-10-20T08:00:00." + "0" * 100 + "Z"
    elif bad == "badStatus":
        value["items"][0]["status"] = "completed"
    elif bad == "badAction":
        value["items"][0]["actionId"] = "jump"
    elif bad == "badId":
        value["items"][0]["requestId"] = "private/request"
    elif bad == "duplicate":
        value["items"].append(history_item())
    elif bad == "missing":
        del value["items"][0]["updatedAt"]
    else:
        value["items"] = "never-return-private-value"
    controller.history_result = value
    response = client.get(BASE + "/history", headers=headers)
    assert response.status_code == 503
    assert response.json()["code"] == "MOTION_UNAVAILABLE"
    assert "never-return-private-value" not in response.text
    assert "executionId" not in response.text


def test_history_controller_exception_is_sanitized(bound):
    client, _, controller, headers, _ = bound

    def unavailable():
        raise RuntimeError("never-return-private-value")

    controller.during = unavailable
    response = client.get(BASE + "/history", headers=headers)
    assert response.status_code == 503
    assert "never-return-private-value" not in response.text
    assert not controller.posts
