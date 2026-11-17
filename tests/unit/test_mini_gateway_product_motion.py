"""Optional genuine product authorization; all device traffic is injected."""

import asyncio
from copy import deepcopy
import json
import sqlite3

import httpx
import pytest

from ai_cat_controller.mini_gateway import motion
from ai_cat_controller.mini_gateway.motion import (
    LocalMotionController, MotionBusy, MotionConflict, MotionDisabled, MotionLocked,
    MotionProductAuthRequired, MotionUnavailable, MotionUnsupported, MotionUnverified,
)


SERIAL = "7c2b63fd4a128"
PRODUCT_PET = "pet_existing_web_owner"
WEB_SESSION = "genuine-placeholder-session-token"
DEVICE_KEY = "device-key-placeholder"
ACTIONS = ("head_nod", "head_shake", "tail_wag", "proud_pose", "quiet_companion", "greeting_combo", "celebration_combo")
HEAD_ACTIONS = {"head_nod", "head_shake", "quiet_companion"}


async def authorized():
    return True


class ProductDevice:
    def __init__(self):
        self.requests = []
        self.current_action = "idle"
        self.serial = SERIAL
        self.pets = [{"pet_id": PRODUCT_PET, "serial_number": SERIAL, "online": True,
                      "owner_user_id": "usr_existing_web_owner"}]
        self.catalog = [{"action_id": action, "available": True, "unlocked": True,
                         "debug_unlocked": False, "parameters_editable": False,
                         "safety_profile": "validated_preset_v1"} for action in ACTIONS]
        self.expired = False
        self.catalog_reads = 0
        self.on_catalog = lambda: None
        self.post_handler = None
        self.status_fields = {}

    @property
    def posts(self):
        return [request for request in self.requests if request.method == "POST"]

    async def __call__(self, request):
        self.requests.append(request)
        path = request.url.path
        if path == "/health":
            return self.response({"status": "ok", "adapter": "local_k1"})
        assert request.headers["x-api-key"] == DEVICE_KEY
        if path == "/api/v1/device/status":
            return self.response({"adapter_mode": "local_k1", "connected": True,
                                  "device_serial": self.serial, "current_action": self.current_action,
                                  **self.status_fields})
        if path == "/api/v1/motion/stop":
            assert "x-mock-session" not in request.headers
            self.current_action = "idle"
            return self.response({"stopped": True, "state": "idle"})
        assert request.headers["x-mock-session"] == WEB_SESSION
        if self.expired:
            return httpx.Response(401, json={"success": False, "message": WEB_SESSION})
        if path == "/api/v1/pets":
            return self.response(deepcopy(self.pets))
        if path == f"/api/v1/pets/{PRODUCT_PET}/actions":
            self.catalog_reads += 1
            self.on_catalog()
            return self.response(deepcopy(self.catalog))
        assert request.method == "POST"
        assert path.startswith(f"/api/v1/pets/{PRODUCT_PET}/actions/") and path.endswith("/execute")
        if self.post_handler:
            return await self.post_handler(request)
        action = path.split("/")[-2]
        assert action in ACTIONS
        self.current_action = action
        return self.response({"execution_id": "run_product_example", "action_id": action,
                              "request_id": json.loads(request.content)["request_id"],
                              "status": "running", "duplicate": False,
                              "result": None, "error": None,
                              "created_at": "2026-10-21T02:00:00Z", "completed_at": None}, 202)

    @staticmethod
    def response(data, status=200):
        return httpx.Response(status, json={"success": True, "data": data})


def controller(tmp_path, device, **overrides):
    arguments = {"db_path": tmp_path / "private" / "motion.sqlite3", "enabled": True,
                 "upstream_url": "http://192.168.1.152:8000", "api_key": DEVICE_KEY,
                 "expected_serial": SERIAL, "product_session": WEB_SESSION,
                 "product_pet_id": PRODUCT_PET, "transport": httpx.MockTransport(device)}
    arguments.update(overrides)
    return LocalMotionController(**arguments)


def intents(client):
    with sqlite3.connect(client.path) as connection:
        return connection.execute("SELECT action_id,status FROM mm_intents ORDER BY seq").fetchall()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ACTIONS)
async def test_all_seven_use_genuine_owned_product_preset_with_scoped_id(tmp_path, action):
    device = ProductDevice()
    client = controller(tmp_path, device)
    result = await client.execute("mini_owner", "mini_pet", action, "stable-request", authorize=authorized)
    assert result["execution"]["actionId"] == action
    assert result["execution"]["requestId"] == "stable-request"
    assert result["execution"]["status"] == "running"
    assert len(device.posts) == 1
    post = device.posts[0]
    assert post.url.path == f"/api/v1/pets/{PRODUCT_PET}/actions/{action}/execute"
    assert json.loads(post.content) == {"request_id": client._product_request_id("mini_owner", "mini_pet", "stable-request")}
    assert 4 <= len(json.loads(post.content)["request_id"]) <= 80
    assert device.catalog_reads >= 2
    assert all("x-mock-session" not in request.headers for request in device.requests
               if "/pets" not in request.url.path)
    assert WEB_SESSION not in repr(result) and PRODUCT_PET not in repr(result)
    assert SERIAL not in repr(result) and DEVICE_KEY not in repr(result)
    raw_ledger = client.path.read_bytes()
    assert WEB_SESSION.encode() not in raw_ledger and PRODUCT_PET.encode() not in raw_ledger
    client.close()


@pytest.mark.asyncio
async def test_exact_seven_catalog_is_safe_and_does_not_expose_product_metadata(tmp_path):
    device = ProductDevice()
    device.catalog[2]["unavailable_reason"] = WEB_SESSION
    device.catalog[2]["available"] = False
    device.catalog[3]["unlocked"] = False
    device.catalog[4]["debug_unlocked"] = True
    client = controller(tmp_path, device)
    snapshot = await client.snapshot()
    assert [item["actionId"] for item in snapshot["actions"]] == list(ACTIONS)
    assert snapshot["verified"] is True
    assert [item["actionId"] for item in snapshot["actions"] if item["available"]] == [*ACTIONS[:2], *ACTIONS[5:]]
    assert WEB_SESSION not in repr(snapshot) and PRODUCT_PET not in repr(snapshot)
    assert not device.posts and intents(client) == []
    client.close()


@pytest.mark.asyncio
async def test_verified_no_tail_requires_exact_head_catalog_and_keeps_natural_policy(tmp_path):
    device = ProductDevice()
    device.status_fields = {"has_tail": False, "motion_enabled": True}
    device.catalog = [item for item in device.catalog if item["action_id"] in HEAD_ACTIONS]
    device.catalog[-1]["unlocked"] = False
    client = controller(tmp_path, device)
    result = await client.snapshot()
    assert result["hasTail"] is False and result["deviceMotionEnabled"] is True
    assert {item["actionId"] for item in result["actions"]} == HEAD_ACTIONS
    assert not next(item for item in result["actions"] if item["actionId"] == "quiet_companion")["available"]
    with pytest.raises(MotionLocked):
        await client.execute("user", "pet", "quiet_companion", "locked", authorize=authorized)
    result = await client.execute("user", "pet", "head_nod", "head", authorize=authorized)
    assert len(result["actions"]) == 3 and result["execution"]["status"] == "running"
    assert len(device.posts) == 1 and "/pets/" in device.posts[0].url.path
    client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("has_tail", [None, True, "missing"])
async def test_three_catalog_never_infers_no_tail_from_missing_actions(tmp_path, has_tail):
    device = ProductDevice()
    if has_tail != "missing":
        device.status_fields = {"has_tail": has_tail}
    device.catalog = [item for item in device.catalog if item["action_id"] in HEAD_ACTIONS]
    client = controller(tmp_path, device)
    result = await client.snapshot()
    assert len(result["actions"]) == 7 and not any(item["available"] for item in result["actions"])
    with pytest.raises((MotionProductAuthRequired, MotionUnverified)):
        await client.execute("user", "pet", "head_nod", "blocked", authorize=authorized)
    assert not device.posts and intents(client) == []
    client.close()


@pytest.mark.asyncio
async def test_false_tail_with_seven_catalog_locks_without_raw_fallback(tmp_path):
    device = ProductDevice()
    device.status_fields = {"has_tail": False}
    client = controller(tmp_path, device)
    result = await client.snapshot()
    assert result["hasTail"] is False and len(result["actions"]) == 3
    assert not any(item["available"] for item in result["actions"])
    with pytest.raises(MotionProductAuthRequired):
        await client.execute("user", "pet", "head_nod", "blocked", authorize=authorized)
    assert not device.posts and intents(client) == []
    client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["has_tail", "motion_enabled"])
@pytest.mark.parametrize("invalid", [None, 0, 1, "false", [], {}])
async def test_explicit_non_boolean_device_policy_fails_closed(tmp_path, field, invalid):
    device = ProductDevice()
    device.status_fields = {field: invalid}
    client = controller(tmp_path, device)
    result = await client.snapshot()
    assert result["verified"] is False and result["hasTail"] is None and result["deviceMotionEnabled"] is None
    assert len(result["actions"]) == 7 and not any(item["available"] for item in result["actions"])
    with pytest.raises(MotionUnverified):
        await client.execute("user", "pet", "head_nod", "blocked", authorize=authorized)
    assert not device.posts and intents(client) == []
    client.close()


@pytest.mark.asyncio
async def test_device_pause_blocks_even_debug_catalog_but_stop_ignores_product_auth(tmp_path):
    device = ProductDevice()
    device.status_fields = {"motion_enabled": False, "has_tail": False}
    device.catalog = [item for item in device.catalog if item["action_id"] in HEAD_ACTIONS]
    for item in device.catalog:
        item["debug_unlocked"] = True
    client = controller(tmp_path, device)
    result = await client.snapshot()
    assert result["enabled"] is True and result["verified"] is True and result["deviceMotionEnabled"] is False
    assert len(result["actions"]) == 3 and not any(item["available"] for item in result["actions"])
    with pytest.raises(MotionDisabled):
        await client.execute("user", "pet", "head_nod", "blocked", authorize=authorized)
    assert not device.posts and intents(client) == []
    device.expired = True
    result = await client.stop("user", "pet", "safety_stop", authorize=authorized)
    assert result["execution"]["status"] == "stopped" and result["deviceMotionEnabled"] is False
    assert [post.url.path for post in device.posts] == ["/api/v1/motion/stop"]
    client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [{"motion_enabled": False}, {"has_tail": False}])
async def test_final_device_policy_change_after_catalog_rejects_reserved_intent(tmp_path, change):
    device = ProductDevice()
    device.status_fields = {"motion_enabled": True, "has_tail": True}
    def flip():
        if device.catalog_reads == 2:
            device.status_fields.update(change)
    device.on_catalog = flip
    client = controller(tmp_path, device)
    with pytest.raises((MotionDisabled, MotionUnverified)):
        await client.execute("user", "pet", "head_nod", "blocked", authorize=authorized)
    assert not device.posts and intents(client) == [("head_nod", "failed")]
    client.close()


@pytest.mark.asyncio
async def test_wrong_serial_cannot_authorize_a_short_catalog(tmp_path):
    device = ProductDevice()
    device.serial = "another_machine"
    device.status_fields = {"has_tail": False}
    device.catalog = [item for item in device.catalog if item["action_id"] in HEAD_ACTIONS]
    client = controller(tmp_path, device)
    result = await client.snapshot()
    assert result["verified"] is False and result["hasTail"] is None and len(result["actions"]) == 7
    assert not any(item["available"] for item in result["actions"])
    client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ACTIONS[2:])
async def test_no_product_config_keeps_extra_actions_locked_without_claiming_or_login(tmp_path, action):
    device = ProductDevice()
    client = controller(tmp_path, device, product_session=None, product_pet_id=None)
    snapshot = await client.snapshot()
    assert [item["actionId"] for item in snapshot["actions"] if item["available"]] == list(ACTIONS[:2])
    assert all(item["reason"] == "需关联网页授权" for item in snapshot["actions"][2:])
    with pytest.raises(MotionProductAuthRequired):
        await client.execute("user", "pet", action, "once", authorize=authorized)
    assert intents(client) == [] and not device.posts
    assert all(request.url.path in ("/health", "/api/v1/device/status") for request in device.requests)
    client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ACTIONS)
@pytest.mark.parametrize("policy,error", [("unlocked", MotionLocked), ("available", MotionUnsupported), ("debug_unlocked", MotionLocked)])
async def test_locked_unsupported_and_debug_override_never_reserve_or_post(tmp_path, action, policy, error):
    device = ProductDevice()
    device.catalog[ACTIONS.index(action)][policy] = policy == "debug_unlocked"
    client = controller(tmp_path, device)
    with pytest.raises(error):
        await client.execute("user", "pet", action, "once", authorize=authorized)
    assert intents(client) == [] and not device.posts
    client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["expired", "wrongPet", "duplicatePet", "wrongSerial", "offline", "missingAction", "duplicateAction", "unknownAction", "badBoolean", "missingDebug", "editable", "wrongProfile", "notList"])
async def test_product_auth_and_catalog_failure_lock_every_action_without_raw_fallback(tmp_path, invalid):
    device = ProductDevice()
    if invalid == "expired":
        device.expired = True
    elif invalid == "wrongPet":
        device.pets[0]["pet_id"] = "another_pet"
    elif invalid == "duplicatePet":
        device.pets.append(deepcopy(device.pets[0]))
    elif invalid == "wrongSerial":
        device.pets[0]["serial_number"] = "7c2WRONGa128"
    elif invalid == "offline":
        device.pets[0]["online"] = False
    elif invalid == "missingAction":
        device.catalog.pop()
    elif invalid == "duplicateAction":
        device.catalog[-1] = deepcopy(device.catalog[0])
    elif invalid == "unknownAction":
        device.catalog[-1]["action_id"] = "arbitrary_preset"
    elif invalid == "badBoolean":
        device.catalog[0]["unlocked"] = 1
    elif invalid == "missingDebug":
        del device.catalog[0]["debug_unlocked"]
    elif invalid == "editable":
        device.catalog[0]["parameters_editable"] = True
    elif invalid == "wrongProfile":
        device.catalog[0]["safety_profile"] = "arbitrary"
    else:
        device.catalog = {"data": device.catalog}
    client = controller(tmp_path, device)
    snapshot = await client.snapshot()
    assert snapshot["verified"] is True and not any(item["available"] for item in snapshot["actions"])
    with pytest.raises(MotionProductAuthRequired) as error:
        await client.execute("user", "pet", "head_nod", "once", authorize=authorized)
    assert WEB_SESSION not in str(error.value) and intents(client) == [] and not device.posts
    client.close()


@pytest.mark.asyncio
async def test_policy_is_rechecked_after_reservation_and_before_any_product_post(tmp_path):
    device = ProductDevice()
    client = controller(tmp_path, device)

    async def revoked_after_reserve():
        assert intents(client) == [("quiet_companion", "sending")]
        device.catalog[ACTIONS.index("quiet_companion")]["unlocked"] = False

    with pytest.raises(MotionLocked):
        await client.execute("user", "pet", "quiet_companion", "once", authorize=revoked_after_reserve)
    assert intents(client) == [("quiet_companion", "failed")]
    assert not device.posts
    client.close()


@pytest.mark.asyncio
async def test_same_request_replays_after_restart_without_another_physical_post(tmp_path):
    device = ProductDevice()
    client = controller(tmp_path, device)
    result = await client.execute("user", "pet", "greeting_combo", "once", authorize=authorized)
    client.close()
    client = controller(tmp_path, device)
    repeated = await client.execute("user", "pet", "greeting_combo", "once", authorize=authorized)
    assert repeated["execution"] == result["execution"] and len(device.posts) == 1
    with pytest.raises(MotionConflict):
        await client.execute("user", "pet", "celebration_combo", "once", authorize=authorized)
    client.close()


def test_upstream_request_namespace_includes_mini_owner_and_pet():
    values = {LocalMotionController._product_request_id(user, pet, "same")
              for user, pet in (("user", "pet"), ("other", "pet"), ("user", "other"))}
    assert len(values) == 3
    assert LocalMotionController._product_request_id("user", "pet", "same") in values


@pytest.mark.asyncio
async def test_product_timeout_latches_review_and_never_retries_even_after_stop(tmp_path):
    device = ProductDevice()

    async def timeout(request):
        raise httpx.ReadTimeout(WEB_SESSION)

    device.post_handler = timeout
    client = controller(tmp_path, device)
    with pytest.raises(MotionUnavailable):
        await client.execute("user", "pet", "proud_pose", "once", authorize=authorized)
    assert intents(client) == [("proud_pose", "unknown")]
    await client.execute("user", "pet", "proud_pose", "once", authorize=authorized)
    assert len(device.posts) == 1
    device.expired = True
    result = await client.stop("user", "pet", "stop", authorize=authorized)
    assert result["execution"]["status"] == "stopped" and result["deviceBusy"] is True
    device.expired = False
    with pytest.raises(MotionBusy):
        await client.execute("user", "pet", "head_nod", "next", authorize=authorized)
    assert len(device.posts) == 2
    client.close()


@pytest.mark.asyncio
async def test_expired_or_hung_product_authorization_cannot_block_stop_or_stop_replay(tmp_path):
    device = ProductDevice()
    client = controller(tmp_path, device)
    await client.execute("user", "pet", "celebration_combo", "once", authorize=authorized)
    original = device.__call__

    async def dispatch(request):
        assert not request.url.path.startswith("/api/v1/pets")
        return await original(request)

    client._transport = httpx.MockTransport(dispatch)
    result = await asyncio.wait_for(client.stop("user", "pet", "stop", authorize=authorized), 0.5)
    assert result["execution"]["status"] == "stopped"
    assert all(not item["available"] for item in result["actions"])
    repeated = await asyncio.wait_for(client.stop("user", "pet", "stop", authorize=authorized), 0.5)
    assert repeated["execution"] == result["execution"]
    assert len(device.posts) == 2 and device.posts[-1].url.path == "/api/v1/motion/stop"
    client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["wrongAction", "wrongId", "duplicate", "completed", "missingExecutionId", "wrongPet", "wrongOwner", "redirect", "tooLarge"])
async def test_unexpected_product_ack_is_unknown_never_completed_or_retried(tmp_path, bad):
    device = ProductDevice()

    async def invalid_ack(request):
        body = {"execution_id": "run_example", "action_id": "tail_wag",
                "request_id": json.loads(request.content)["request_id"], "status": "running", "duplicate": False}
        if bad == "wrongAction":
            body["action_id"] = "head_nod"
        elif bad == "wrongId":
            body["request_id"] = "different"
        elif bad == "duplicate":
            body["duplicate"] = True
        elif bad == "completed":
            body["status"] = "completed"
        elif bad == "missingExecutionId":
            del body["execution_id"]
        elif bad == "wrongPet":
            body["pet_id"] = "another_pet"
        elif bad == "wrongOwner":
            body["user_id"] = "another_owner"
        elif bad == "redirect":
            return httpx.Response(307, headers={"Location": "http://8.8.8.8/"})
        elif bad == "tooLarge":
            return httpx.Response(202, text="x" * 65537)
        return device.response(body, 202)

    device.post_handler = invalid_ack
    client = controller(tmp_path, device)
    with pytest.raises(MotionUnavailable) as error:
        await client.execute("user", "pet", "tail_wag", "once", authorize=authorized)
    assert WEB_SESSION not in str(error.value) and intents(client) == [("tail_wag", "unknown")]
    await client.execute("user", "pet", "tail_wag", "once", authorize=authorized)
    assert len(device.posts) == 1
    client.close()


@pytest.mark.parametrize("options", [
    {"product_session": None}, {"product_pet_id": None}, {"product_session": "short"},
    {"product_session": "token with spaces"}, {"product_pet_id": "../other"},
    {"product_session": 123}, {"api_key": ""}, {"upstream_url": None},
])
def test_product_config_requires_explicit_valid_genuine_session_and_pinned_device(tmp_path, options):
    with pytest.raises(ValueError):
        controller(tmp_path, ProductDevice(), **options)


@pytest.mark.asyncio
async def test_product_history_uses_same_bounded_owner_scoped_local_journal(tmp_path):
    device = ProductDevice()
    client = controller(tmp_path, device)
    await client.execute("user", "pet", "quiet_companion", "once", authorize=authorized)
    result = await client.history(user_id="user", pet_id="pet")
    assert result["limit"] == 20 and result["items"][0]["actionId"] == "quiet_companion"
    assert (await client.history(user_id="other", pet_id="pet"))["items"] == []
    assert WEB_SESSION not in repr(result) and PRODUCT_PET not in repr(result)
    client.close()


@pytest.mark.asyncio
async def test_physical_serial_is_rechecked_after_all_product_policy_awaits(tmp_path):
    device = ProductDevice()
    client = controller(tmp_path, device)

    def change_after_second_catalog():
        if device.catalog_reads == 2:
            device.serial = "7c2OTHERa128"

    device.on_catalog = change_after_second_catalog
    with pytest.raises(MotionUnverified):
        await client.execute("user", "pet", "head_nod", "once", authorize=authorized)
    assert intents(client) == [("head_nod", "failed")] and not device.posts
    client.close()


@pytest.mark.asyncio
async def test_changed_product_owner_is_not_accepted_by_same_web_session(tmp_path):
    device = ProductDevice()
    client = controller(tmp_path, device)
    assert all(item["available"] for item in (await client.snapshot())["actions"])
    device.pets[0]["owner_user_id"] = "different_web_owner"
    assert not any(item["available"] for item in (await client.snapshot())["actions"])
    with pytest.raises(MotionProductAuthRequired):
        await client.execute("user", "pet", "quiet_companion", "once", authorize=authorized)
    assert not device.posts and intents(client) == []
    client.close()


@pytest.mark.asyncio
async def test_preflight_deadline_before_reserve_cannot_dispatch_late(tmp_path, monkeypatch):
    monkeypatch.setattr(motion, "_PREFLIGHT_TIMEOUT", 0.03)
    device = ProductDevice()
    original = device.__call__

    async def delayed(request):
        if request.url.path == "/api/v1/pets":
            await asyncio.sleep(0.1)
        return await original(request)

    client = controller(tmp_path, device, transport=httpx.MockTransport(delayed))
    with pytest.raises(MotionUnavailable):
        await client.execute("user", "pet", "head_nod", "once", authorize=authorized)
    await asyncio.sleep(0.12)
    assert not device.posts and intents(client) == []
    with sqlite3.connect(client.path) as connection:
        assert connection.execute("SELECT value FROM mm_meta WHERE key='manual_review'").fetchone() == ("0",)
    client.close()


@pytest.mark.asyncio
async def test_preflight_deadline_after_reserve_marks_failed_not_unknown_and_no_late_post(tmp_path, monkeypatch):
    monkeypatch.setattr(motion, "_PREFLIGHT_TIMEOUT", 0.05)
    device = ProductDevice()
    client = controller(tmp_path, device)

    async def delayed_authorization():
        await asyncio.sleep(0.12)
        return True

    with pytest.raises(MotionUnavailable):
        await client.execute("user", "pet", "head_nod", "once", authorize=delayed_authorization)
    await asyncio.sleep(0.13)
    assert not device.posts and intents(client) == [("head_nod", "failed")]
    with sqlite3.connect(client.path) as connection:
        assert connection.execute("SELECT value FROM mm_meta WHERE key='manual_review'").fetchone() == ("0",)
    client.close()


@pytest.mark.asyncio
async def test_entire_product_policy_has_one_bounded_timeout_not_per_get_sum(tmp_path, monkeypatch):
    monkeypatch.setattr(motion, "_TIMEOUT", 0.08)
    device = ProductDevice()
    original = device.__call__

    async def delayed(request):
        if request.url.path.startswith("/api/v1/pets"):
            await asyncio.sleep(0.05)
        return await original(request)

    client = controller(tmp_path, device, transport=httpx.MockTransport(delayed))
    with pytest.raises(MotionProductAuthRequired):
        await asyncio.wait_for(client._product_policy(), 0.12)
    assert not device.posts
    client.close()


@pytest.mark.asyncio
async def test_final_mini_authorization_after_product_checks_is_required(tmp_path):
    device = ProductDevice()
    client = controller(tmp_path, device)
    checks = 0

    async def revoke_at_last_check():
        nonlocal checks
        checks += 1
        return checks != 2

    with pytest.raises(MotionUnverified):
        await client.execute("user", "pet", "tail_wag", "once", authorize=revoke_at_last_check)
    assert not device.posts and intents(client) == [("tail_wag", "failed")]
    client.close()
