"""Actual application/service leases with a second cooperative coordinator."""

import asyncio
from contextlib import asynccontextmanager
import os
import sys

import httpx
import pytest

from ai_cat_controller.core.config import Settings
from ai_cat_controller.core.errors import ActionConflictError
from ai_cat_controller.core.operation_gate import DeviceOperationGate
from ai_cat_controller.core.process_gate import ProcessGateError, ProcessOperationGate
from ai_cat_controller.main import create_app
from ai_cat_controller.adapters.factory import create_adapter
from ai_cat_controller.adapters.command_runner import CommandRunner


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(.001)


@asynccontextmanager
async def stack(tmp_path):
    path = tmp_path / "coordination"
    provisioned = ProcessOperationGate.provision(path)
    provisioned.close()
    settings = Settings(hardware_driver="mock", process_gate_path=path,
        data_path=tmp_path / "product.sqlite3", dialog_config_path=tmp_path / "dialog.json",
        personality_runtime_path=tmp_path / "personality.json", dialog_event_path=tmp_path / "events.jsonl",
        dialog_text_request_path=tmp_path / "text.json", enable_product_data_reset=True,
        motion_cooldown_seconds=0)
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic") as client:
            login = await client.post("/api/v1/auth/mock-login", json={"login_code": "process-bridge", "nickname": "合成用户"})
            assert login.status_code == 200
            data = login.json()["data"]
            headers = {"X-Mock-Session": data["session_token"]}
            response = await client.post("/api/v1/pets/bind", headers=headers,
                json={"device_serial": "PROCESS-BRIDGE-001", "pet_name": "合成猫", "network_name": "Synthetic"})
            assert response.status_code == 200
            other = ProcessOperationGate(path)
            try:
                yield app, client, headers, response.json()["data"]["pet"]["pet_id"], data["user"]["user_id"], other
            finally:
                other.close()


@pytest.mark.asyncio
async def test_configured_main_ordinary_write_really_holds_cross_process_work_lock(tmp_path):
    async with stack(tmp_path) as (app, _, _, _, _, other):
        gate = app.state.services.maintenance_gate
        entered, release = asyncio.Event(), asyncio.Event()
        async def work():
            entered.set()
            await release.wait()
        task = asyncio.create_task(gate.operation(work()))
        await entered.wait()
        maintenance = other.begin_maintenance()
        try:
            assert maintenance.try_exclusive() is False
        finally:
            release.set()
            await task
        assert maintenance.try_exclusive() is True
        maintenance.finish()


@pytest.mark.asyncio
async def test_external_maintenance_blocks_http_writes_but_not_reads_stop_or_interrupt(tmp_path):
    async with stack(tmp_path) as (_, client, headers, pet, _, other):
        maintenance = other.begin_maintenance()
        assert maintenance.try_exclusive()
        try:
            denied = await client.patch(f"/api/v1/pets/{pet}/settings", headers=headers, json={"name": "禁止旧写"})
            assert denied.status_code == 409
            assert (await client.get("/api/v1/device/status")).status_code == 200
            assert (await client.post("/api/v1/motion/stop")).status_code == 200
            assert (await client.post("/api/v1/dialog/interrupt", json={"request_id": "safe-interrupt"})).status_code == 200
        finally:
            maintenance.finish()


@pytest.mark.asyncio
async def test_old_ingress_in_another_worker_is_not_new_work_after_external_maintenance(tmp_path):
    async with stack(tmp_path) as (app, _, _, pet, user, other):
        gate = app.state.services.maintenance_gate
        with gate.request_scope():
            maintenance = other.begin_maintenance()
            assert maintenance.try_exclusive()
            maintenance.finish()
            with pytest.raises(ActionConflictError):
                await app.state.services.product.feedback(user_id=user, pet_id=pet,
                    category="other", content="原请求不能跨进程维护继续写入")
        result = await app.state.services.product.feedback(user_id=user, pet_id=pet,
            category="other", content="用户重新确认后的新请求")
        assert result["category"] == "other"


@pytest.mark.asyncio
async def test_cross_worker_maintenance_waits_cancelled_callers_actual_work(tmp_path):
    path = tmp_path / "coordination"
    first = ProcessOperationGate.provision(path)
    second = ProcessOperationGate(path)
    old_gate = DeviceOperationGate(process_gate=first)
    new_gate = DeviceOperationGate(process_gate=second)
    entered, release = asyncio.Event(), asyncio.Event()
    erased = False
    async def write():
        entered.set()
        await release.wait()
    async def erase():
        nonlocal erased
        erased = True
    caller = asyncio.create_task(old_gate.operation(write()))
    await entered.wait()
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    maintenance = asyncio.create_task(new_gate.maintenance(erase()))
    try:
        await until(lambda: new_gate.closing)
        assert not maintenance.done() and not erased
        release.set()
        await maintenance
        assert erased and old_gate.active_operations == 0
    finally:
        release.set()
        await asyncio.gather(maintenance, return_exceptions=True)
        await old_gate.shutdown()
        await new_gate.shutdown()
        first.close()
        second.close()


@pytest.mark.asyncio
async def test_required_child_reserved_during_other_worker_closing_keeps_original_work_lease(tmp_path):
    path = tmp_path / "coordination"
    first = ProcessOperationGate.provision(path)
    second = ProcessOperationGate(path)
    gate = DeviceOperationGate(process_gate=first)
    started, reserve, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()
    children = []
    async def parent():
        started.set()
        await reserve.wait()
        children.append(gate.spawn(finish.wait()))
    original = asyncio.create_task(gate.operation(parent()))
    await started.wait()
    maintenance = second.begin_maintenance()
    try:
        reserve.set()
        await original
        assert maintenance.try_exclusive() is False
        finish.set()
        await children[0]
        assert maintenance.try_exclusive() is True
    finally:
        finish.set()
        await asyncio.gather(original, *children, return_exceptions=True)
        maintenance.finish()
        await gate.shutdown()
        first.close()
        second.close()


@pytest.mark.asyncio
async def test_drain_deadline_cannot_erase_while_an_external_actual_lease_is_alive(tmp_path):
    path = tmp_path / "coordination"
    process = ProcessOperationGate.provision(path)
    other = ProcessOperationGate(path)
    gate = DeviceOperationGate(process_gate=process, timeout_seconds=.02)
    lease = other.normal()
    erased = False
    async def erase():
        nonlocal erased
        erased = True
    try:
        with pytest.raises(ActionConflictError):
            await gate.maintenance(erase())
        await until(lambda: not gate.closing)
        assert not erased
        # Abort did not pretend the external work ended; its own lease remains.
        maintenance = process.begin_maintenance()
        assert not maintenance.try_exclusive()
        maintenance.finish()
    finally:
        lease.close()
        await gate.shutdown()
        process.close()
        other.close()


@pytest.mark.parametrize("value", ["", "relative", "/tmp/../coord", "/tmp//coord", "/tmp/coord/", "/tmp/coord\n"])
def test_process_gate_setting_rejects_ambiguous_paths(value):
    with pytest.raises(ValueError):
        Settings.from_env({"AI_CAT_PROCESS_GATE_PATH": value})


@pytest.mark.asyncio
@pytest.mark.parametrize("configured", [None, "/tmp/explicit-process-coordination"])
async def test_actual_native_command_environment_uses_explicit_settings_not_another_gate(monkeypatch, configured):
    captured = {}
    monkeypatch.setenv("AI_CAT_PROCESS_GATE_PATH", "/tmp/other-ambient-coordination")
    class InertProcess:
        returncode = 0
        async def communicate(self):
            return b"", b""
    async def inert_exec(*args, **kwargs):
        captured.update(kwargs["env"])
        return InertProcess()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", inert_exec)
    adapter = create_adapter(Settings(hardware_driver="local_k1", process_gate_path=configured,
        api_key_enabled=True, api_key="synthetic-process-bridge-only-key"))
    # Only the runner is exercised; the fixed stop is stubbed, never executed.
    await adapter._runner.run("/usr/bin/ai-toy_app", ["motor", "stop"])
    if configured is None:
        assert "AI_CAT_PROCESS_GATE_PATH" not in captured
    else:
        assert captured["AI_CAT_PROCESS_GATE_PATH"] == configured


@pytest.mark.asyncio
async def test_real_opaque_command_receives_work_fd_not_an_exclusive_admission_lock(tmp_path, monkeypatch):
    path = tmp_path / "coordination"
    process = ProcessOperationGate.provision(path)
    gate = DeviceOperationGate(process_gate=process)
    script = ("import os,stat; fd=int(os.environ['SYNTHETIC_WORK_FD']); "
        "actual=os.fstat(fd); expected=os.stat(os.environ['SYNTHETIC_WORK_PATH']); "
        "assert stat.S_ISREG(actual.st_mode) and (actual.st_dev,actual.st_ino)==(expected.st_dev,expected.st_ino); "
        "print('inherited-work-lease')")
    executable = sys.executable
    arguments = ["-c", script]
    runner = CommandRunner(allowed_executables=frozenset({executable}), allowed_services=frozenset(),
        timeout_seconds=2, allowed_commands={executable: frozenset({tuple(arguments)})})
    original_exec = asyncio.create_subprocess_exec
    captured = []
    async def observed_exec(*args, **kwargs):
        descriptors = kwargs.get("pass_fds", ())
        assert len(descriptors) == 1
        captured.append(descriptors[0])
        kwargs["env"] = {**kwargs["env"], "SYNTHETIC_WORK_FD": str(descriptors[0]),
            "SYNTHETIC_WORK_PATH": str(path / "work.lock")}
        return await original_exec(*args, **kwargs)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", observed_exec)
    try:
        result = await gate.operation(runner.run(executable, arguments))
        assert result.returncode == 0 and result.stdout == "inherited-work-lease"
        with pytest.raises(OSError):
            os.fstat(captured[0])
        maintenance = process.begin_maintenance()
        try:
            assert maintenance.try_exclusive()
        finally:
            maintenance.finish()
    finally:
        await gate.shutdown()
        process.close()
