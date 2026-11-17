"""Fixed-preset gateway motion tests: all device I/O uses MockTransport."""

from __future__ import annotations

import asyncio
import gzip
import json
import os
from pathlib import Path
import sqlite3
import traceback

import httpx
import pytest

from ai_cat_controller.mini_gateway import motion
from ai_cat_controller.mini_gateway.motion import (
    LocalMotionController, MotionBusy, MotionConflict, MotionDisabled,
    MotionUnavailable, MotionUnverified,
)


SERIAL = "7c2b63fd4a128"
TOKEN = "not-a-real-device-secret"
ORIGIN = "http://192.168.1.152:8000"


async def authorize():
    return None


class Device:
    def __init__(self):
        self.requests = []
        self.serial = SERIAL
        self.adapter = "local_k1"
        self.connected = True
        self.current_action = "idle"
        self.post_response = None
        self.status_fields = {}

    async def __call__(self, request):
        self.requests.append(request)
        path = request.url.path
        if path == "/health":
            return httpx.Response(200, json={"success": True, "data": {"status": "ok", "adapter": self.adapter, "version": "0.1.0"}})
        if path == "/api/v1/device/status":
            return httpx.Response(200, json={"success": True, "data": {"adapter_mode": self.adapter, "connected": self.connected, "device_serial": self.serial, "current_action": self.current_action, "last_error": TOKEN, **self.status_fields}})
        if self.post_response:
            return await self.post_response(request)
        if path == "/api/v1/motion/stop":
            self.current_action = "idle"
            return httpx.Response(200, json={"success": True, "data": {"stopped": True, "state": "idle"}})
        action = {"/api/v1/motion/head/nod": "head_nod", "/api/v1/motion/head/shake": "head_shake"}[path]
        self.current_action = action
        return httpx.Response(202, json={"success": True, "data": {"action": action, "request_id": json.loads(request.content)["request_id"], "state": "running"}})

    @property
    def posts(self):
        return [request for request in self.requests if request.method == "POST"]


def controller(tmp_path, device=None, **options):
    device = device or Device()
    arguments = {"db_path": tmp_path / "private" / "motion.sqlite3", "enabled": True, "upstream_url": ORIGIN, "api_key": TOKEN, "expected_serial": SERIAL, "transport": httpx.MockTransport(device)}
    arguments.update(options)
    return LocalMotionController(**arguments)


def expire_cooldown(controller):
    with sqlite3.connect(controller.path) as connection:
        connection.execute("UPDATE mm_meta SET value='0' WHERE key='cooldown'")


def assert_safe(error):
    assert error.__context__ is None
    assert error.__cause__ is None
    rendered = "".join(traceback.format_exception(error))
    assert TOKEN not in rendered
    assert ORIGIN not in rendered
    assert SERIAL not in rendered


@pytest.mark.asyncio
async def test_default_is_disabled_unconfigured_and_never_posts(tmp_path):
    device = Device()
    client = LocalMotionController(db_path=tmp_path / "private" / "motion.sqlite3", transport=httpx.MockTransport(device))
    result = await client.snapshot()
    assert result["enabled"] is False and result["verified"] is False
    assert result["execution"] is None and result["deviceBusy"] is False
    assert [item["actionId"] for item in result["actions"]] == list(motion._ACTIONS)
    assert not any(item["available"] for item in result["actions"])
    for operation in (client.execute("usr", "pet", "head_nod", "request", authorize=authorize), client.stop("usr", "pet", "stop", authorize=authorize)):
        with pytest.raises(MotionDisabled):
            await operation
    assert device.requests == []


@pytest.mark.asyncio
async def test_no_tail_without_web_auth_only_exposes_two_raw_heads_in_three_cards(tmp_path):
    device = Device()
    device.status_fields = {"has_tail": False, "motion_enabled": True}
    client = controller(tmp_path, device)
    result = await client.snapshot()
    assert result["hasTail"] is False and {item["actionId"] for item in result["actions"]} == {"head_nod", "head_shake", "quiet_companion"}
    assert {item["actionId"] for item in result["actions"] if item["available"]} == {"head_nod", "head_shake"}
    quiet = next(item for item in result["actions"] if item["actionId"] == "quiet_companion")
    assert quiet["reason"] == "需关联网页授权" and not quiet["available"]
    result = await client.execute("user", "pet", "head_nod", "fixed_head", authorize=authorize)
    assert result["execution"]["status"] == "running"
    assert [item.url.path for item in device.posts] == ["/api/v1/motion/head/nod"]
    client.close()


@pytest.mark.asyncio
async def test_legacy_missing_device_policy_is_null_not_invented_true(tmp_path):
    device = Device()
    client = controller(tmp_path, device)
    result = await client.snapshot()
    assert result["hasTail"] is None and result["deviceMotionEnabled"] is None
    assert len(result["actions"]) == 7
    client.close()


@pytest.mark.asyncio
async def test_paused_raw_heads_reject_before_intent_but_independent_stop_still_posts(tmp_path):
    device = Device()
    device.status_fields = {"has_tail": False, "motion_enabled": False}
    client = controller(tmp_path, device)
    with pytest.raises(MotionDisabled):
        await client.execute("user", "pet", "head_nod", "blocked", authorize=authorize)
    assert not device.posts
    result = await client.stop("user", "pet", "stop", authorize=authorize)
    assert result["execution"]["status"] == "stopped" and result["hasTail"] is False
    assert result["deviceMotionEnabled"] is False
    assert [item.url.path for item in device.posts] == ["/api/v1/motion/stop"]
    client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["has_tail", "motion_enabled"])
async def test_invalid_execution_policy_does_not_block_stop_but_never_relaxes_identity(tmp_path, field):
    device = Device()
    device.status_fields = {field: "false"}
    client = controller(tmp_path, device)
    with pytest.raises(MotionUnverified):
        await client.execute("user", "pet", "head_nod", "blocked", authorize=authorize)
    result = await client.stop("user", "pet", "stop", authorize=authorize)
    assert result["execution"]["status"] == "stopped" and result["verified"] is False
    assert result["hasTail"] is None and result["deviceMotionEnabled"] is None
    assert [item.url.path for item in device.posts] == ["/api/v1/motion/stop"]
    device.serial = "different_machine"
    with pytest.raises(MotionUnverified):
        await client.stop("user", "pet", "stop_wrong_device", authorize=authorize)
    assert len(device.posts) == 1
    client.close()


@pytest.mark.asyncio
async def test_disabled_configured_reads_but_rejects_all_writes(tmp_path):
    device = Device()
    client = controller(tmp_path, device, enabled=False)
    result = await client.snapshot()
    assert result["verified"] is True
    assert not any(item["available"] for item in result["actions"])
    with pytest.raises(MotionDisabled):
        await client.execute("usr", "pet", "head_nod", "request", authorize=authorize)
    with pytest.raises(MotionDisabled):
        await client.stop("usr", "pet", "stop", authorize=authorize)
    assert len(device.requests) == 2 and not device.posts


@pytest.mark.parametrize("origin", [
    "http://example.com:8000", "http://8.8.8.8:8000", "http://169.254.1.1", "http://0.0.0.0", "http://224.0.0.1", "http://192.168.1.152/api/v1", "http://user:secret@192.168.1.152", "http://192.168.1.152?x=1", "http://192.168.1.152#fragment", "http://192.168.1.152?", "http://192.168.1.152#", "http://192.168.1.152\n", "file:///tmp/device", "https://127.0.0.1", "http://[::]", "http://[fe80::1]",
])
def test_only_literal_private_origin_is_accepted(tmp_path, origin):
    with pytest.raises(ValueError, match="private device origin"):
        controller(tmp_path, upstream_url=origin)


@pytest.mark.parametrize("origin", ["http://127.0.0.1:2222", "http://10.0.0.2", "http://172.16.0.1", "http://[::1]", "http://[fd01::1]:8000"])
def test_private_literal_origins_are_supported(tmp_path, origin):
    client = controller(tmp_path, upstream_url=origin)
    client.close()


@pytest.mark.parametrize("configuration", [{"api_key": ""}, {"upstream_url": None}, {"expected_serial": None}, {"enabled": "true"}, {"api_key": "bad\nkey"}, {"expected_serial": "bad serial"}])
def test_enablement_requires_complete_valid_server_configuration(tmp_path, configuration):
    with pytest.raises(ValueError):
        controller(tmp_path, **configuration)


@pytest.mark.asyncio
@pytest.mark.parametrize("action,path", [("head_nod", "/api/v1/motion/head/nod"), ("head_shake", "/api/v1/motion/head/shake")])
async def test_fixed_preset_body_intent_is_committed_before_post(tmp_path, action, path):
    device = Device()
    client = controller(tmp_path, device)
    original = device.__call__
    authorized = []

    async def check_authorization():
        authorized.append(True)

    async def dispatch(request):
        if request.method == "POST":
            assert authorized
            with sqlite3.connect(client.path) as connection:
                row = connection.execute("SELECT action_id,status FROM mm_intents").fetchone()
            assert row == (action, "sending")
        return await original(request)

    client._transport = httpx.MockTransport(dispatch)
    result = await client.execute("usr", "pet", action, "once", authorize=check_authorization)
    assert result["execution"]["status"] == "running"
    assert result["execution"]["actionId"] == action
    assert result["deviceBusy"] is True
    assert len(device.posts) == 1 and device.posts[0].url.path == path
    assert json.loads(device.posts[0].content) == {"request_id": "once"}
    assert all(request.headers.get("x-api-key") == TOKEN for request in device.requests if request.url.path != "/health")
    assert all("x-api-key" not in request.headers for request in device.requests if request.url.path == "/health")
    assert TOKEN not in repr(result) and SERIAL not in repr(result)
    assert result["sampledAt"].endswith("Z") and result["execution"]["updatedAt"].endswith("Z")


@pytest.mark.asyncio
async def test_request_replay_persists_across_restart_and_returns_original_execution(tmp_path):
    device = Device()
    client = controller(tmp_path, device)
    original = await client.execute("usr", "pet", "head_nod", "once", authorize=authorize)
    device.current_action = "idle"
    await client.snapshot(user_id="usr", pet_id="pet")
    expire_cooldown(client)
    await client.execute("usr", "pet", "head_shake", "second", authorize=authorize)
    client.close()
    restarted = controller(tmp_path, device)
    replay = await restarted.execute("usr", "pet", "head_nod", "once", authorize=authorize)
    assert len(device.posts) == 2
    assert replay["execution"]["executionId"] == original["execution"]["executionId"]
    assert replay["execution"]["status"] == "ended_unverified"


@pytest.mark.asyncio
async def test_reused_request_id_cannot_change_action_or_stop_target(tmp_path):
    device = Device()
    client = controller(tmp_path, device)
    result = await client.execute("usr", "pet", "head_nod", "same", authorize=authorize)
    with pytest.raises(MotionConflict):
        await client.execute("usr", "pet", "head_shake", "same", authorize=authorize)
    with pytest.raises(MotionConflict):
        await client.stop("usr", "pet", "same", authorize=authorize)
    await client.stop("usr", "pet", "stop", result["execution"]["executionId"], authorize=authorize)
    with pytest.raises(MotionConflict):
        await client.stop("usr", "pet", "stop", None, authorize=authorize)


@pytest.mark.asyncio
async def test_different_owners_or_pets_cannot_bypass_global_exclusion(tmp_path):
    device = Device()
    client = controller(tmp_path, device)
    await client.execute("usr", "pet", "head_nod", "same", authorize=authorize)
    device.current_action = "idle"
    for user, pet in [("other", "pet"), ("usr", "other")]:
        with pytest.raises(MotionBusy):
            await client.execute(user, pet, "head_nod", "same", authorize=authorize)
    assert len(device.posts) == 1


@pytest.mark.asyncio
async def test_new_owner_cannot_read_old_execution(tmp_path):
    client = controller(tmp_path)
    await client.execute("old", "pet", "head_nod", "secret-request", authorize=authorize)
    assert (await client.snapshot(user_id="new", pet_id="pet"))["execution"] is None
    assert (await client.snapshot(user_id="old", pet_id="other"))["execution"] is None
    assert (await client.snapshot())["execution"] is None
    assert (await client.snapshot(user_id="old", pet_id="pet"))["execution"]["requestId"] == "secret-request"


@pytest.mark.asyncio
async def test_idle_is_not_claimed_as_success_and_starts_short_click_throttle(tmp_path):
    device = Device()
    client = controller(tmp_path, device)
    await client.execute("usr", "pet", "head_nod", "once", authorize=authorize)
    device.current_action = "idle"
    result = await client.snapshot(user_id="usr", pet_id="pet")
    assert result["execution"]["status"] == "ended_unverified"
    assert not any(action["available"] for action in result["actions"])
    with pytest.raises(MotionBusy):
        await client.execute("usr", "pet", "head_nod", "next", authorize=authorize)
    expire_cooldown(client)
    assert [action["actionId"] for action in (await client.snapshot())["actions"] if action["available"]] == ["head_nod", "head_shake"]


@pytest.mark.asyncio
async def test_short_idle_throttle_does_not_extend_on_poll_and_allows_next_distinct_action(tmp_path, monkeypatch):
    now = [1_790_000_000.0]
    monkeypatch.setattr(motion.time, "time", lambda: now[0])
    device = Device()
    client = controller(tmp_path, device)
    try:
        first = await client.execute("usr", "pet", "head_nod", "first", authorize=authorize)
        assert first["execution"]["status"] == "running"
        # Time alone never releases a busy device or its active intent.
        now[0] += 4.0
        busy = await client.snapshot(user_id="usr", pet_id="pet")
        assert busy["deviceBusy"] is True and not any(a["available"] for a in busy["actions"])
        with pytest.raises(MotionBusy):
            await client.execute("usr", "pet", "head_shake", "second", authorize=authorize)
        assert len(device.posts) == 1

        device.current_action = "idle"
        idle_time = now[0]
        ended = await client.snapshot(user_id="usr", pet_id="pet")
        assert ended["execution"]["status"] == "ended_unverified"
        with sqlite3.connect(client.path) as connection:
            deadline = float(connection.execute("SELECT value FROM mm_meta WHERE key='cooldown'").fetchone()[0])
        assert 0.3 <= deadline - idle_time <= 0.5
        assert not any(a["available"] for a in ended["actions"])
        now[0] = idle_time + 0.2
        await client.snapshot(user_id="usr", pet_id="pet")
        with sqlite3.connect(client.path) as connection:
            assert float(connection.execute("SELECT value FROM mm_meta WHERE key='cooldown'").fetchone()[0]) == deadline
        with pytest.raises(MotionBusy):
            await client.execute("usr", "pet", "head_shake", "second", authorize=authorize)

        # No artificial three-second post-completion wait and no local replay.
        now[0] = deadline + 0.01
        ready = await client.snapshot(user_id="usr", pet_id="pet")
        assert {a["actionId"] for a in ready["actions"] if a["available"]} == {"head_nod", "head_shake"}
        second = await client.execute("usr", "pet", "head_shake", "second", authorize=authorize)
        assert second["execution"]["actionId"] == "head_shake" and second["execution"]["status"] == "running"
        replay = await client.execute("usr", "pet", "head_nod", "first", authorize=authorize)
        assert replay["execution"]["status"] == "ended_unverified"
        assert [request.url.path for request in device.posts] == ["/api/v1/motion/head/nod", "/api/v1/motion/head/shake"]
    finally:
        client.close()


@pytest.mark.asyncio
async def test_stop_bypasses_click_throttle_then_has_only_short_nonrenewing_guard(tmp_path, monkeypatch):
    now = [1_790_000_000.0]
    monkeypatch.setattr(motion.time, "time", lambda: now[0])
    device = Device()
    client = controller(tmp_path, device)
    try:
        first = await client.execute("usr", "pet", "head_nod", "first", authorize=authorize)
        stopped = await client.stop("usr", "pet", "stop-first", first["execution"]["executionId"], authorize=authorize)
        assert stopped["execution"]["status"] == "stopped"
        assert [p.url.path for p in device.posts] == ["/api/v1/motion/head/nod", "/api/v1/motion/stop"]
        with sqlite3.connect(client.path) as connection:
            deadline = float(connection.execute("SELECT value FROM mm_meta WHERE key='cooldown'").fetchone()[0])
        assert 0.3 <= deadline - now[0] <= 0.5
        now[0] = deadline + 0.01
        ready = await client.snapshot(user_id="usr", pet_id="pet")
        assert any(a["available"] for a in ready["actions"])
        await client.execute("usr", "pet", "head_shake", "next", authorize=authorize)
        assert len(device.posts) == 3
    finally:
        client.close()


@pytest.mark.asyncio
async def test_elapsed_short_throttle_never_releases_unknown_even_after_stop_ack(tmp_path, monkeypatch):
    now = [1_790_000_000.0]
    monkeypatch.setattr(motion.time, "time", lambda: now[0])
    device = Device()

    async def no_receipt(request):
        raise httpx.ReadTimeout("missing receipt")

    device.post_response = no_receipt
    client = controller(tmp_path, device)
    try:
        with pytest.raises(MotionUnavailable):
            await client.execute("usr", "pet", "head_nod", "uncertain", authorize=authorize)
        now[0] += 60.0
        snapshot = await client.snapshot(user_id="usr", pet_id="pet")
        assert snapshot["execution"]["status"] == "unknown" and snapshot["deviceBusy"] is True
        device.post_response = None
        await client.stop("usr", "pet", "stop-uncertain", authorize=authorize)
        now[0] += 60.0
        snapshot = await client.snapshot(user_id="usr", pet_id="pet")
        assert snapshot["deviceBusy"] is True and not any(a["available"] for a in snapshot["actions"])
        with pytest.raises(MotionBusy):
            await client.execute("usr", "pet", "head_shake", "next", authorize=authorize)
        assert len(device.posts) == 2
    finally:
        client.close()


@pytest.mark.asyncio
async def test_timeout_is_unknown_never_replayed_even_after_restart(tmp_path):
    device = Device()

    async def timeout(request):
        raise httpx.ReadTimeout(TOKEN + ORIGIN)

    device.post_response = timeout
    client = controller(tmp_path, device)
    with pytest.raises(MotionUnavailable) as failure:
        await client.execute("usr", "pet", "head_nod", "once", authorize=authorize)
    assert_safe(failure.value)
    result = await client.snapshot(user_id="usr", pet_id="pet")
    assert result["execution"]["status"] == "unknown" and result["deviceBusy"] is True
    client.close()
    restarted = controller(tmp_path, device)
    replay = await restarted.execute("usr", "pet", "head_nod", "once", authorize=authorize)
    assert replay["execution"]["status"] == "unknown" and len(device.posts) == 1
    with pytest.raises(MotionBusy):
        await restarted.execute("usr", "pet", "head_shake", "next", authorize=authorize)


@pytest.mark.asyncio
async def test_unknown_manual_review_latch_survives_acknowledged_stop(tmp_path):
    device = Device()

    async def timeout(request):
        raise httpx.ReadTimeout("no response")

    device.post_response = timeout
    client = controller(tmp_path, device)
    with pytest.raises(MotionUnavailable):
        await client.execute("usr", "pet", "head_nod", "once", authorize=authorize)
    device.post_response = None
    result = await client.stop("usr", "pet", "stop-unknown", authorize=authorize)
    assert result["execution"]["actionId"] == "stop" and result["execution"]["status"] == "stopped"
    assert result["deviceBusy"] is True
    assert all(action["reason"] == "此前请求结果不确定，需现场核查" for action in result["actions"])
    assert json.loads(device.posts[-1].content) == {} and device.posts[-1].url.path == "/api/v1/motion/stop"
    replay = await client.stop("usr", "pet", "stop-unknown", authorize=authorize)
    assert replay["execution"] == result["execution"] and len(device.posts) == 2
    with pytest.raises(MotionBusy):
        await client.execute("usr", "pet", "head_shake", "next", authorize=authorize)


@pytest.mark.asyncio
async def test_stopping_idle_device_ack_is_not_false_motion_completion(tmp_path):
    device = Device()

    async def idle(request):
        return httpx.Response(200, json={"success": True, "data": {"stopped": False, "state": "idle"}})

    device.post_response = idle
    client = controller(tmp_path, device)
    result = await client.stop("usr", "pet", "stop", authorize=authorize)
    assert result["execution"]["status"] == "stopped"
    assert result["execution"]["actionId"] == "stop"


@pytest.mark.asyncio
async def test_stop_does_not_wait_for_hung_execute_and_late_ack_cannot_resurrect(tmp_path):
    device = Device()
    posted = asyncio.Event()
    release = asyncio.Event()

    async def dispatch(request):
        if request.url.path.endswith("/stop"):
            return httpx.Response(200, json={"success": True, "data": {"stopped": True, "state": "idle"}})
        posted.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            # Simulate a remote server accepting while local request was cancelled.
            return httpx.Response(202, json={"success": True, "data": {"action": "head_nod", "request_id": "once", "state": "running"}})
        return httpx.Response(202, json={"success": True, "data": {"action": "head_nod", "request_id": "once", "state": "running"}})

    device.post_response = dispatch
    client = controller(tmp_path, device)
    executing = asyncio.create_task(client.execute("usr", "pet", "head_nod", "once", authorize=authorize))
    await asyncio.wait_for(posted.wait(), 1)
    stopped = await asyncio.wait_for(client.stop("usr", "pet", "stop", authorize=authorize), 0.5)
    release.set()
    await executing
    assert stopped["execution"]["status"] == "stopped"
    with sqlite3.connect(client.path) as connection:
        assert {row[0] for row in connection.execute("SELECT status FROM mm_intents")} == {"stopped"}


@pytest.mark.asyncio
async def test_stop_supersedes_execution_waiting_for_authorization(tmp_path):
    device = Device()
    waiting = asyncio.Event()
    forever = asyncio.Event()

    async def paused():
        waiting.set()
        await forever.wait()

    client = controller(tmp_path, device)
    execution = asyncio.create_task(client.execute("usr", "pet", "head_nod", "once", authorize=paused))
    await asyncio.wait_for(waiting.wait(), 1)
    await asyncio.wait_for(client.stop("usr", "pet", "stop", authorize=authorize), 0.5)
    await asyncio.gather(execution, return_exceptions=True)
    assert [request.url.path for request in device.posts] == ["/api/v1/motion/stop"]


@pytest.mark.asyncio
async def test_authorization_exception_propagates_without_any_post(tmp_path):
    class Revoked(Exception):
        pass

    async def revoked():
        raise Revoked("expired session")

    device = Device()
    client = controller(tmp_path, device)
    with pytest.raises(Revoked):
        await client.execute("usr", "pet", "head_nod", "once", authorize=revoked)
    assert not device.posts
    assert (await client.snapshot(user_id="usr", pet_id="pet"))["execution"]["status"] == "failed"
    with pytest.raises(Revoked):
        await client.stop("usr", "pet", "stop", authorize=revoked)
    assert not device.posts


@pytest.mark.asyncio
async def test_serial_is_rechecked_after_awaited_authorization(tmp_path):
    device = Device()
    client = controller(tmp_path, device)

    async def switched():
        device.serial = "7c2wrongdevicea128"  # Same masked serial, different full identity.

    with pytest.raises(MotionUnverified):
        await client.execute("usr", "pet", "head_nod", "once", authorize=switched)
    assert not device.posts


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("serial", "7c2anotherdevicea128"), ("adapter", "mock"), ("connected", False), ("connected", 1), ("current_action", "unavailable")])
async def test_unverified_device_is_never_actionable(tmp_path, field, value):
    device = Device()
    setattr(device, field, value)
    client = controller(tmp_path, device)
    result = await client.snapshot()
    assert result["verified"] is False and not any(action["available"] for action in result["actions"])
    with pytest.raises(MotionUnverified):
        await client.execute("usr", "pet", "head_nod", "once", authorize=authorize)
    with pytest.raises(MotionUnverified):
        await client.stop("usr", "pet", "stop", authorize=authorize)
    assert not device.posts


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [
    httpx.Response(200, json={"success": True, "data": {"action": "head_nod", "request_id": "once", "state": "running"}}),
    httpx.Response(202, json={"success": True, "data": {"action": "head_shake", "request_id": "once", "state": "running"}}),
    httpx.Response(202, json={"success": True, "data": {"action": "head_nod", "request_id": "wrong", "state": "running"}}),
    httpx.Response(202, json={"success": True, "data": {"action": "head_nod", "request_id": "once", "state": "completed"}}),
    httpx.Response(202, json={"success": True, "data": {"action": "head_nod", "request_id": "once", "state": "running", "secret": TOKEN}}),
    httpx.Response(307, headers={"Location": "http://8.8.8.8"}),
    httpx.Response(202, text="not JSON " + TOKEN),
    httpx.Response(202, text="x" * 65537),
    httpx.Response(202, content=gzip.compress(b"{}"), headers={"Content-Encoding": "gzip"}),
])
async def test_ambiguous_post_response_becomes_unknown_without_raw_error(tmp_path, invalid):
    device = Device()

    async def respond(request):
        return invalid

    device.post_response = respond
    client = controller(tmp_path, device)
    with pytest.raises(MotionUnavailable) as error:
        await client.execute("usr", "pet", "head_nod", "once", authorize=authorize)
    assert_safe(error.value)
    assert (await client.snapshot(user_id="usr", pet_id="pet"))["execution"]["status"] == "unknown"
    assert len(device.posts) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["已有动作正在执行: head_nod", "动作冷却中，请稍后重试"])
async def test_complete_scheduler_rejection_is_failed_and_never_latches_unknown(tmp_path, message):
    device = Device()

    async def rejected(request):
        # Autonomous motion can start after the last preflight GET. The
        # managed scheduler rejects this submission before creating a task.
        device.current_action = "head_nod"
        return httpx.Response(409, json={"success": False, "message": message,
                                        "data": {"code": "action_conflict", "details": {}}})

    device.post_response = rejected
    client = controller(tmp_path, device)
    with pytest.raises(MotionBusy) as error:
        await client.execute("usr", "pet", "head_shake", "race", authorize=authorize)
    assert_safe(error.value)
    snapshot = await client.snapshot(user_id="usr", pet_id="pet")
    assert snapshot["execution"]["status"] == "failed"
    with sqlite3.connect(client.path) as connection:
        assert connection.execute("SELECT value FROM mm_meta WHERE key='manual_review'").fetchone()[0] == "0"
    replay = await client.execute("usr", "pet", "head_shake", "race", authorize=authorize)
    assert replay["execution"]["status"] == "failed" and len(device.posts) == 1
    device.current_action = "idle"
    expire_cooldown(client)
    snapshot = await client.snapshot(user_id="usr", pet_id="pet")
    assert {a["actionId"] for a in snapshot["actions"] if a["available"]} == {"head_nod", "head_shake"}
    client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status,payload", [
    (409, {"success": False, "message": "已有动作正在执行: head_nod", "data": {"code": "other", "details": {}}}),
    (409, {"success": False, "message": "动作失败", "data": {"code": "action_conflict", "details": {}}}),
    (409, {"success": False, "message": "已有动作正在执行: head_nod", "data": {"code": "action_conflict", "details": {"unverified": True}}}),
    (409, {"success": False, "message": "已有动作正在执行: head_nod", "data": {"code": "action_conflict"}}),
    (500, {"success": False, "message": "已有动作正在执行: head_nod", "data": {"code": "action_conflict", "details": {}}}),
])
async def test_incomplete_or_unrecognized_rejection_stays_unknown(tmp_path, status, payload):
    device = Device()

    async def rejected(request):
        return httpx.Response(status, json=payload)

    device.post_response = rejected
    client = controller(tmp_path, device)
    with pytest.raises(MotionUnavailable):
        await client.execute("usr", "pet", "head_nod", "uncertain", authorize=authorize)
    assert (await client.snapshot(user_id="usr", pet_id="pet"))["execution"]["status"] == "unknown"
    with sqlite3.connect(client.path) as connection:
        assert connection.execute("SELECT value FROM mm_meta WHERE key='manual_review'").fetchone()[0] == "1"
    assert len(device.posts) == 1
    client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [{"stopped": "yes", "state": "idle"}, {"stopped": True, "state": "running"}, {"stopped": True, "state": "idle", "extra": TOKEN}, {}])
async def test_stop_shape_must_be_strict_before_releasing_unknown(tmp_path, data):
    device = Device()

    async def respond(request):
        return httpx.Response(200, json={"success": True, "data": data})

    device.post_response = respond
    client = controller(tmp_path, device)
    with pytest.raises(MotionUnavailable):
        await client.stop("usr", "pet", "stop", authorize=authorize)
    assert (await client.snapshot(user_id="usr", pet_id="pet"))["execution"]["status"] == "unknown"
    with pytest.raises(MotionBusy):
        await client.execute("usr", "pet", "head_nod", "next", authorize=authorize)


@pytest.mark.asyncio
async def test_http_client_has_no_redirect_proxy_or_unbounded_timeout(tmp_path, monkeypatch):
    settings = []
    original = httpx.AsyncClient

    def tracked(*args, **kwargs):
        settings.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(motion.httpx, "AsyncClient", tracked)
    client = controller(tmp_path)
    await client.snapshot()
    assert settings
    assert all(value["trust_env"] is False and value["follow_redirects"] is False for value in settings)
    assert all(0 < getattr(value["timeout"], kind) <= 2.5 for value in settings for kind in ("connect", "read", "write", "pool"))


@pytest.mark.asyncio
async def test_concurrent_different_requests_only_one_submission(tmp_path):
    device = Device()
    client = controller(tmp_path, device)
    results = await asyncio.gather(*[client.execute("usr", "pet", "head_nod", f"request-{number}", authorize=authorize) for number in range(10)], return_exceptions=True)
    assert sum(isinstance(result, dict) for result in results) == 1
    assert all(isinstance(result, (dict, MotionBusy)) for result in results)
    assert len(device.posts) == 1


@pytest.mark.asyncio
async def test_concurrent_same_request_only_one_submission(tmp_path):
    device = Device()
    client = controller(tmp_path, device)
    results = await asyncio.gather(*[client.execute("usr", "pet", "head_nod", "same", authorize=authorize) for _ in range(10)])
    assert len(device.posts) == 1
    assert len({result["execution"]["executionId"] for result in results}) == 1


def test_ledger_rejects_second_live_instance_and_accepts_quiet_restart(tmp_path):
    client = controller(tmp_path)
    with pytest.raises(ValueError):
        controller(tmp_path)
    client.close()
    replacement = controller(tmp_path)
    replacement.close()


@pytest.mark.asyncio
async def test_crashed_sending_intent_is_unknown_on_restart(tmp_path):
    client = controller(tmp_path)
    execution, _ = client._reserve("usr", "pet", "once", "head_nod", None)
    client.close()
    device = Device()
    restarted = controller(tmp_path, device)
    snapshot = await restarted.snapshot(user_id="usr", pet_id="pet")
    assert snapshot["execution"]["executionId"] == execution and snapshot["execution"]["status"] == "unknown"
    await restarted.execute("usr", "pet", "head_nod", "once", authorize=authorize)
    assert not device.posts


def test_private_ledger_does_not_open_session_or_product_tables(tmp_path):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    path = directory / "sessions.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE mg_sessions(id TEXT)")
    path.chmod(0o600)
    before = path.read_bytes()
    with pytest.raises(ValueError):
        controller(tmp_path, db_path=path)
    assert path.read_bytes() == before


def test_ledger_is_private_and_cannot_switch_device_identity(tmp_path):
    client = controller(tmp_path)
    path = client.path
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    client.close()
    with pytest.raises(ValueError):
        controller(tmp_path, expected_serial="different-device")


@pytest.mark.parametrize("mode", [0o777, 0o770, 0o755])
def test_ledger_rejects_nonprivate_parent(tmp_path, mode):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    directory.chmod(mode)
    with pytest.raises(ValueError):
        controller(tmp_path)
    assert not (directory / "motion.sqlite3").exists()


def test_ledger_rejects_symlinks_without_touching_target(tmp_path):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    target = tmp_path / "target"
    target.write_bytes(b"not a database")
    (directory / "motion.sqlite3").symlink_to(target)
    with pytest.raises(ValueError):
        controller(tmp_path)
    assert target.read_bytes() == b"not a database"


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", [("", "pet", "head_nod", "req"), ("usr", "pet", "jump", "req"), ("usr", "pet", "head_nod", "bad request"), ("usr", "pet", "head_nod", "x" * 65)])
async def test_invalid_requests_never_reach_device(tmp_path, arguments):
    device = Device()
    client = controller(tmp_path, device)
    with pytest.raises(MotionConflict):
        await client.execute(*arguments, authorize=authorize)
    assert not device.requests


@pytest.mark.asyncio
async def test_stop_cannot_reference_another_owners_execution(tmp_path):
    device = Device()
    client = controller(tmp_path, device)
    result = await client.execute("usr", "pet", "head_nod", "once", authorize=authorize)
    with pytest.raises(MotionConflict):
        await client.stop("other", "pet", "stop", result["execution"]["executionId"], authorize=authorize)
    assert len(device.posts) == 1


@pytest.mark.asyncio
async def test_stop_for_old_execution_cannot_stop_a_newer_action(tmp_path):
    device = Device()
    client = controller(tmp_path, device)
    first = await client.execute("usr", "pet", "head_nod", "first", authorize=authorize)
    old_id = first["execution"]["executionId"]
    device.current_action = "idle"
    await client.snapshot(user_id="usr", pet_id="pet")
    expire_cooldown(client)
    newer = await client.execute("usr", "pet", "head_shake", "second", authorize=authorize)
    assert newer["execution"]["executionId"] != old_id
    with pytest.raises(MotionConflict):
        await client.stop("usr", "pet", "old-stop", old_id, authorize=authorize)
    assert len(device.posts) == 2
    assert device.current_action == "head_shake"


@pytest.mark.asyncio
async def test_closed_controller_stop_does_not_contact_device(tmp_path):
    device = Device()
    client = controller(tmp_path, device)
    client.close()
    with pytest.raises(MotionUnavailable):
        await client.stop("usr", "pet", "stop", authorize=authorize)
    assert not device.posts


@pytest.mark.asyncio
async def test_stop_rechecks_authorization_before_mutating_or_cancelling_pending_intents(tmp_path):
    device = Device()
    client = controller(tmp_path, device)
    first = await client.execute("usr", "pet", "head_nod", "first", authorize=authorize)

    async def revoked():
        raise PermissionError("revoked")

    with pytest.raises(PermissionError):
        await client.stop("usr", "pet", "stop", first["execution"]["executionId"], authorize=revoked)
    assert len(device.posts) == 1
    current = await client.snapshot(user_id="usr", pet_id="pet")
    assert current["execution"]["status"] == "running"
    assert current["execution"]["executionId"] == first["execution"]["executionId"]


def seed_history(client, *, user="usr", pet="pet", count=1, prefix="entry"):
    with sqlite3.connect(client.path) as connection:
        for index in range(count):
            connection.execute(
                "INSERT INTO mm_intents(execution_id,user_id,pet_id,request_id,action_id,target_id,status,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (f"mx_{prefix}_{index}", user, pet, f"{prefix}_{index}",
                 ("head_nod", "head_shake", "stop")[index % 3], None,
                 ("sending", "running", "unknown", "stopped", "ended_unverified", "failed")[index % 6],
                 "2026-10-20T08:00:00Z"),
            )


@pytest.mark.asyncio
async def test_history_is_empty_by_default_without_device_io(tmp_path):
    device = Device()
    client = LocalMotionController(db_path=tmp_path / "private" / "motion.sqlite3",
                                   transport=httpx.MockTransport(device))
    assert await client.history(user_id="usr", pet_id="pet") == {"items": [], "limit": 20}
    assert device.requests == []


@pytest.mark.asyncio
async def test_history_is_fixed_bounded_newest_first_and_isolated_by_owner_and_pet(tmp_path):
    device = Device()
    client = controller(tmp_path, device, enabled=False)
    seed_history(client, count=25)
    seed_history(client, user="other", count=23, prefix="other_owner")
    seed_history(client, pet="other", count=24, prefix="other_pet")
    result = await client.history(user_id="usr", pet_id="pet")
    assert result["limit"] == 20 and len(result["items"]) == 20
    assert [item["requestId"] for item in result["items"]] == [f"entry_{index}" for index in range(24, 4, -1)]
    assert {item["actionId"] for item in result["items"]} == {"head_nod", "head_shake", "stop"}
    assert all(set(item) == {"executionId", "actionId", "status", "requestId", "updatedAt"} for item in result["items"])
    assert await client.history(user_id="unknown", pet_id="pet") == {"items": [], "limit": 20}
    assert device.requests == []


@pytest.mark.asyncio
async def test_history_preserves_all_intents_metadata_and_unknown_review_latch(tmp_path):
    device = Device()
    client = controller(tmp_path, device)
    seed_history(client, count=6)
    with sqlite3.connect(client.path) as connection:
        connection.execute("UPDATE mm_meta SET value='1' WHERE key='manual_review'")
        connection.execute("UPDATE mm_meta SET value='9999999999' WHERE key='cooldown'")
        before = list(connection.iterdump())
    result = await client.history(user_id="usr", pet_id="pet")
    assert {item["status"] for item in result["items"]} == {"sending", "running", "unknown", "stopped", "ended_unverified", "failed"}
    with sqlite3.connect(client.path) as connection:
        assert list(connection.iterdump()) == before
    assert device.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("user,pet", [(None, "pet"), ("usr", None), ("", "pet"), ("usr", "bad/id"), ("usr", "x" * 129)])
async def test_history_rejects_invalid_identity_without_device_io(tmp_path, user, pet):
    device = Device()
    client = controller(tmp_path, device)
    with pytest.raises(MotionConflict):
        await client.history(user_id=user, pet_id=pet)
    assert device.requests == []


@pytest.mark.asyncio
async def test_closed_history_is_unavailable_without_device_io(tmp_path):
    device = Device()
    client = controller(tmp_path, device)
    client.close()
    with pytest.raises(MotionUnavailable):
        await client.history(user_id="usr", pet_id="pet")
    assert device.requests == []
