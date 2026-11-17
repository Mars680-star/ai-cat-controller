"""Local adapter mutation boundaries using only synthetic commands and IPC."""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from pathlib import Path

import pytest

from ai_cat_controller.adapters.command_runner import CommandResult, CommandRunner
from ai_cat_controller.adapters.local_k1 import LocalK1Adapter
from ai_cat_controller.adapters.factory import create_adapter
from ai_cat_controller.core.config import Settings
from ai_cat_controller.core.errors import ActionConflictError
from ai_cat_controller.core.operation_gate import DeviceOperationGate, current_process_work_lease
from ai_cat_controller.core.process_gate import ProcessOperationGate


def settings(path=None, **changes):
    values = {"hardware_driver": "local_k1", "process_gate_path": path,
              "api_key_enabled": True, "api_key": "synthetic-test-key"}
    values.update(changes)
    return Settings(**values)


def ready_file(path):
    path.write_text(json.dumps({"state": "ready", "session_active": False,
        "updated_at_ms": int(time.time() * 1000), "pid": os.getpid()}), encoding="utf-8")


class Runner:
    def __init__(self):
        self.calls = []
        self.leases = []

    async def run(self, executable, args):
        self.calls.append((executable, tuple(args)))
        self.leases.append(current_process_work_lease())
        if args[0] == "is-active":
            return CommandResult(0, "active", "", False)
        if args[0] == "get-sink-volume":
            return CommandResult(0, "Volume: 42%", "", False)
        if args[0] == "get-sink-mute":
            return CommandResult(0, "Mute: no", "", False)
        if args == ["motor", "stop"]:
            return CommandResult(0, "stop requested", "", False)
        return CommandResult(0, "", "", False)


@pytest.fixture
def protocol(tmp_path):
    gate = ProcessOperationGate.provision(tmp_path / "gate")
    yield gate
    gate.close()


@pytest.mark.asyncio
async def test_injected_gate_is_borrowed_and_adapter_close_does_not_close_it(protocol):
    gate = DeviceOperationGate(process_gate=protocol)
    runner = Runner()
    adapter = LocalK1Adapter(settings(protocol.path), runner, operation_gate=gate)
    await adapter.set_output_volume(37)
    assert [call[1] for call in runner.calls] == [
        ("set-sink-volume", "@DEFAULT_SINK@", "37%"), ("set-sink-mute", "@DEFAULT_SINK@", "0")]
    assert all(lease is not None for lease in runner.leases)
    await adapter.close()
    assert await gate.operation(asyncio.sleep(0, result="still usable")) == "still usable"
    await gate.shutdown()
    assert protocol.snapshot() is not None


@pytest.mark.asyncio
async def test_factory_local_adapter_borrows_the_explicit_shared_gate(protocol):
    gate = DeviceOperationGate(process_gate=protocol)
    adapter = create_adapter(settings(protocol.path), operation_gate=gate)
    try:
        assert isinstance(adapter, LocalK1Adapter)
        assert adapter._normal_operation_gate() is gate
        await adapter.close()
        assert await gate.operation(asyncio.sleep(0, result="still usable")) == "still usable"
        assert protocol.snapshot() is not None
    finally:
        await adapter.close()
        await gate.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["volume", "wake", "text", "speak", "preset", "head", "tail", "ensure", "signal"])
async def test_direct_mutators_fail_closed_before_commands_or_data_when_closing(tmp_path, protocol, method):
    status, request = tmp_path / "status.json", tmp_path / "request.json"
    ready_file(status)
    runner = Runner()
    adapter = LocalK1Adapter(settings(protocol.path, enable_tail_motion=True,
        motion_profile="k1_vendor_smooth", dialog_status_path=status,
        dialog_text_request_path=request), runner)
    maintenance = protocol.begin_maintenance()
    functions = {
        "volume": lambda: adapter.set_output_volume(37), "wake": adapter.wake_dialog,
        "text": lambda: adapter.send_text_dialog("hello", "one"),
        "speak": lambda: adapter.speak_text("hello", "one"),
        "preset": lambda: adapter.run_motion_preset("conversation_nod", 500),
        "head": lambda: adapter._run_head_motor("head_lr", 0.35, 500),
        "tail": lambda: adapter._run_tail_motor(0.35, 500),
        "ensure": adapter._ensure_dialog_ready,
        "signal": lambda: adapter._signal_dialog("SIGUSR1"),
    }
    try:
        with pytest.raises(ActionConflictError):
            await functions[method]()
        assert runner.calls == []
        assert not request.exists() and not request.with_suffix(".json.tmp").exists()
        assert maintenance.try_exclusive()
    finally:
        await adapter.close()
        maintenance.finish()


@pytest.mark.asyncio
async def test_direct_constructor_opens_protocol_lazily_and_never_provisions(tmp_path):
    path = tmp_path / "missing"
    runner = Runner()
    adapter = LocalK1Adapter(settings(path), runner)
    assert not path.exists()
    with pytest.raises(ActionConflictError):
        await adapter.set_output_volume(38)
    assert not path.exists() and runner.calls == []
    await adapter.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("injected_domain", ["disabled", "other"])
async def test_injected_gate_cannot_disable_or_switch_explicit_protocol(tmp_path, protocol, injected_domain):
    other = ProcessOperationGate.provision(tmp_path / "other") if injected_domain == "other" else None
    gate = DeviceOperationGate(process_gate=other)
    runner = Runner()
    adapter = LocalK1Adapter(settings(protocol.path), runner, operation_gate=gate)
    try:
        with pytest.raises(ActionConflictError):
            await adapter.set_output_volume(38)
        assert runner.calls == []
    finally:
        await adapter.close()
        await gate.shutdown()
        if other is not None:
            other.close()


@pytest.mark.asyncio
async def test_reading_missing_protocol_does_not_open_or_create_it(tmp_path):
    path = tmp_path / "missing"
    runner = Runner()
    adapter = LocalK1Adapter(settings(path), runner)
    try:
        assert (await adapter._read_output_volume_status())["output_volume_percent"] == 42
        await adapter.get_services_status()
        assert not path.exists()
        assert all(lease is None for lease in runner.leases)
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_volume_second_step_settles_in_same_lease_after_closing(protocol):
    maintenance = []

    class ClosingRunner(Runner):
        async def run(self, executable, args):
            result = await super().run(executable, args)
            if args[0] == "set-sink-volume":
                maintenance.append(protocol.begin_maintenance())
                assert not maintenance[0].try_exclusive()
            return result

    runner = ClosingRunner()
    adapter = LocalK1Adapter(settings(protocol.path), runner)
    try:
        await adapter.set_output_volume(38)
        assert [call[1][0] for call in runner.calls] == ["set-sink-volume", "set-sink-mute"]
        assert all(lease is not None for lease in runner.leases)
        assert runner.leases[0].ticket == runner.leases[1].ticket
        await adapter.close()
        assert maintenance[0].try_exclusive()
    finally:
        await adapter.close()
        for item in maintenance:
            item.finish()


@pytest.mark.asyncio
async def test_existing_outer_scope_ticket_is_not_refreshed_by_direct_adapter(protocol):
    outer = DeviceOperationGate(process_gate=protocol)
    runner = Runner()
    adapter = LocalK1Adapter(settings(protocol.path), runner)
    with outer.request_scope():
        await outer.maintenance(asyncio.sleep(0))
        with pytest.raises(ActionConflictError):
            await adapter.set_output_volume(39)
    assert runner.calls == []
    await adapter.close()
    await outer.shutdown()


@pytest.mark.asyncio
async def test_admitted_outer_lease_continues_required_direct_mutation_while_closing(protocol):
    outer = DeviceOperationGate(process_gate=protocol)
    runner = Runner()
    adapter = LocalK1Adapter(settings(protocol.path), runner)
    maintenance = []

    async def transaction():
        maintenance.append(protocol.begin_maintenance())
        await adapter.set_output_volume(40)

    try:
        await outer.operation(transaction())
        assert [call[1][0] for call in runner.calls] == ["set-sink-volume", "set-sink-mute"]
        assert all(lease is not None for lease in runner.leases)
        await adapter.close()
        # Closing the direct adapter must not close the outer borrowed gate.
        assert maintenance[0].try_exclusive()
    finally:
        await adapter.close()
        for item in maintenance:
            item.finish()
        await outer.shutdown()


@pytest.mark.asyncio
async def test_cancelled_text_caller_cannot_abandon_live_file_thread_or_signal(tmp_path, protocol):
    entered, release = threading.Event(), threading.Event()
    status, request = tmp_path / "status.json", tmp_path / "request.json"
    ready_file(status)
    runner = Runner()
    adapter = LocalK1Adapter(settings(protocol.path, dialog_status_path=status,
        dialog_text_request_path=request), runner)
    original = adapter._write_dialog_request

    def blocked_write(*args):
        entered.set()
        assert release.wait(3)
        original(*args)

    adapter._write_dialog_request = blocked_write
    task = asyncio.create_task(adapter.send_text_dialog("synthetic only", "one"))
    maintenance = None
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        maintenance = protocol.begin_maintenance()
        assert not maintenance.try_exclusive()
        release.set()
        await adapter.close()
        assert request.exists()
        assert runner.calls[-1][1] == ("kill", "--signal=SIGHUP", "volc-conv-ai.service")
        assert runner.leases[-1] is not None
        assert maintenance.try_exclusive()
    finally:
        release.set()
        await adapter.close()
        if maintenance is not None:
            maintenance.finish()


@pytest.mark.asyncio
async def test_cancelling_adapter_close_never_drops_its_live_required_thread(tmp_path, protocol):
    entered, release = threading.Event(), threading.Event()
    status, request = tmp_path / "status.json", tmp_path / "request.json"
    ready_file(status)
    runner = Runner()
    adapter = LocalK1Adapter(settings(protocol.path, dialog_status_path=status,
        dialog_text_request_path=request), runner)
    original = adapter._write_dialog_request

    def blocked_write(*args):
        entered.set()
        assert release.wait(3)
        original(*args)

    adapter._write_dialog_request = blocked_write
    mutation = asyncio.create_task(adapter.speak_text("synthetic only", "one"))
    closing = None
    maintenance = None
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        closing = asyncio.create_task(adapter.close())
        await asyncio.sleep(0)
        assert not closing.done()
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing
        maintenance = protocol.begin_maintenance()
        assert not maintenance.try_exclusive()
        release.set()
        await mutation
        await adapter.close()
        assert request.exists()
        assert runner.calls[-1][1] == ("kill", "--signal=SIGHUP", "volc-conv-ai.service")
        assert maintenance.try_exclusive()
    finally:
        release.set()
        if closing is not None:
            await asyncio.gather(closing, return_exceptions=True)
        await asyncio.gather(mutation, return_exceptions=True)
        await adapter.close()
        if maintenance is not None:
            maintenance.finish()


@pytest.mark.asyncio
@pytest.mark.parametrize("injected", [False, True])
async def test_owned_coordinator_close_waits_for_mutation_borrowing_outer_gate(tmp_path, protocol, injected):
    outer = DeviceOperationGate(process_gate=protocol)
    entered, release = threading.Event(), threading.Event()
    status, request = tmp_path / "status.json", tmp_path / "request.json"
    ready_file(status)
    runner = Runner()
    adapter = LocalK1Adapter(settings(protocol.path, dialog_status_path=status,
        dialog_text_request_path=request), runner, operation_gate=outer if injected else None)
    original = adapter._write_dialog_request

    def blocked_write(*args):
        entered.set()
        assert release.wait(3)
        original(*args)

    adapter._write_dialog_request = blocked_write
    mutation = asyncio.create_task(outer.operation(adapter.speak_text("synthetic only", "one")))
    closing = None
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        closing = asyncio.create_task(adapter.close())
        await asyncio.sleep(0)
        assert not closing.done(), "Owned coordinator closed before borrowed transaction settled"
        release.set()
        await mutation
        await closing
        assert request.exists()
        assert runner.calls[-1][1] == ("kill", "--signal=SIGHUP", "volc-conv-ai.service")
        assert await outer.operation(asyncio.sleep(0, result="borrowed gate remains open"))
    finally:
        release.set()
        await asyncio.gather(mutation, return_exceptions=True)
        if closing is not None:
            await asyncio.gather(closing, return_exceptions=True)
        await adapter.close()
        await outer.shutdown()


@pytest.mark.asyncio
async def test_delayed_raw_child_cannot_refresh_inactive_outer_context_after_external_maintenance(protocol):
    outer = DeviceOperationGate(process_gate=protocol)
    runner = Runner()
    adapter = LocalK1Adapter(settings(protocol.path), runner)
    release = asyncio.Event()
    tasks = []

    async def delayed():
        await release.wait()
        await adapter.set_output_volume(42)

    async def old_request():
        tasks.append(asyncio.create_task(delayed()))

    try:
        await outer.operation(old_request())
        await asyncio.sleep(0)
        maintenance = protocol.begin_maintenance()
        try:
            assert maintenance.try_exclusive()
        finally:
            maintenance.finish()
        release.set()
        with pytest.raises(ActionConflictError):
            await tasks[0]
        assert runner.calls == []
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await adapter.close()
        await outer.shutdown()


@pytest.mark.asyncio
async def test_reads_and_safety_commands_do_not_need_ordinary_admission(tmp_path, protocol):
    status = tmp_path / "status.json"
    ready_file(status)
    runner = Runner()
    adapter = LocalK1Adapter(settings(protocol.path, dialog_status_path=status,
        battery_supply_path=tmp_path / "no-battery", charger_supply_path=tmp_path / "no-charger"), runner)
    maintenance = protocol.begin_maintenance()
    try:
        assert maintenance.try_exclusive()
        assert (await adapter.get_dialog_status())["state"] == "ready"
        await adapter.get_device_status()
        await adapter.get_services_status()
        assert await adapter.stop_motion()
        await adapter.interrupt_dialog()
        assert runner.calls[-1][1] == ("kill", "--signal=SIGUSR2", "volc-conv-ai.service")
        assert all(lease is None for lease in runner.leases)
    finally:
        await adapter.close()
        maintenance.finish()


@pytest.mark.asyncio
async def test_direct_adapter_lease_reaches_command_runner_pass_fds(tmp_path, protocol, monkeypatch):
    seen = []

    class Process:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def spawn(*args, **kwargs):
        descriptor, = kwargs["pass_fds"]
        os.fstat(descriptor)
        seen.append(descriptor)
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    runner = CommandRunner(allowed_executables=frozenset({"/usr/bin/pactl"}),
        allowed_services=frozenset(), timeout_seconds=1, allowed_commands={"/usr/bin/pactl": frozenset({
            ("set-sink-volume", "@DEFAULT_SINK@", "41%"),
            ("set-sink-mute", "@DEFAULT_SINK@", "0")})})
    adapter = LocalK1Adapter(settings(protocol.path), runner)
    try:
        await adapter.set_output_volume(41)
        assert len(seen) == 2
        for descriptor in seen:
            with pytest.raises(OSError):
                os.fstat(descriptor)
    finally:
        await adapter.close()
