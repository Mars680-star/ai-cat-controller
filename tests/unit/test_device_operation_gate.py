import asyncio
import inspect
import time

import pytest

from ai_cat_controller.core.errors import ActionConflictError
from ai_cat_controller.core.operation_gate import DeviceOperationGate


async def _until(predicate):
    async with asyncio.timeout(1):
        while not predicate():
            await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_normal_nested_calls_share_the_actual_work_lease():
    gate = DeviceOperationGate()

    async def inner():
        assert gate.active_operations == 1
        return 7

    async def outer():
        return await gate.operation(inner())

    assert await gate.operation(outer()) == 7
    assert gate.active_operations == 0


@pytest.mark.asyncio
async def test_maintenance_drains_existing_work_and_rejects_new_operations():
    gate = DeviceOperationGate()
    release = asyncio.Event()
    entered = asyncio.Event()
    order = []

    async def work():
        order.append("old-start")
        entered.set()
        await release.wait()
        order.append("old-finished")

    async def erase():
        assert gate.active_operations == 0
        order.append("erase")

    old = asyncio.create_task(gate.operation(work()))
    await entered.wait()
    maintenance = asyncio.create_task(gate.maintenance(erase()))
    await _until(lambda: gate.closing)
    rejected = work()
    with pytest.raises(ActionConflictError):
        await gate.operation(rejected)
    assert inspect.getcoroutinestate(rejected) == inspect.CORO_CLOSED
    assert order == ["old-start"]
    release.set()
    await asyncio.gather(old, maintenance)
    assert order == ["old-start", "old-finished", "erase"]
    assert not gate.closing


@pytest.mark.asyncio
async def test_cancelled_caller_does_not_release_actual_work_lease():
    gate = DeviceOperationGate()
    release = asyncio.Event()
    completed = False

    async def work():
        nonlocal completed
        await release.wait()
        completed = True

    caller = asyncio.create_task(gate.operation(work()))
    await _until(lambda: gate.active_operations == 1)
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert gate.active_operations == 1

    async def erase():
        assert completed

    maintenance = asyncio.create_task(gate.maintenance(erase()))
    await _until(lambda: gate.closing)
    assert not maintenance.done()
    release.set()
    await maintenance


@pytest.mark.asyncio
async def test_timeout_never_runs_destructive_body_or_resumes_an_old_ticket():
    gate = DeviceOperationGate(timeout_seconds=0.02)
    release = asyncio.Event()
    erased = False

    async def erase():
        nonlocal erased
        erased = True

    old = asyncio.create_task(gate.operation(release.wait()))
    await _until(lambda: gate.active_operations == 1)
    with gate.request_scope():
        with pytest.raises(ActionConflictError):
            await gate.maintenance(erase())
        assert not erased and not gate.closing
        with pytest.raises(ActionConflictError):
            await gate.operation(asyncio.sleep(0))
    release.set()
    await old
    assert await gate.operation(asyncio.sleep(0, result=1)) == 1


@pytest.mark.asyncio
async def test_request_captured_before_maintenance_cannot_write_afterwards():
    gate = DeviceOperationGate()
    with gate.request_scope():
        await gate.maintenance(asyncio.sleep(0))
        with pytest.raises(ActionConflictError):
            await gate.operation(asyncio.sleep(0))
    assert await gate.operation(asyncio.sleep(0, result=2)) == 2


@pytest.mark.asyncio
async def test_normal_work_cannot_upgrade_itself_into_maintenance():
    gate = DeviceOperationGate()

    async def work():
        with pytest.raises(ActionConflictError):
            await gate.maintenance(asyncio.sleep(0))
        assert not gate.closing

    await gate.operation(work())


@pytest.mark.asyncio
async def test_spawn_reserves_work_before_it_is_scheduled():
    gate = DeviceOperationGate()
    release = asyncio.Event()
    task = gate.spawn(release.wait())
    assert gate.active_operations == 1
    maintenance = asyncio.create_task(gate.maintenance(asyncio.sleep(0)))
    await _until(lambda: gate.closing)
    assert not maintenance.done()
    release.set()
    await asyncio.gather(task, maintenance)


@pytest.mark.asyncio
async def test_admitted_parent_can_reserve_required_finalizer_while_closing():
    gate = DeviceOperationGate()
    entered, go, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()
    children = []

    async def parent():
        entered.set()
        await go.wait()
        children.append(gate.spawn(finish.wait()))

    original = asyncio.create_task(gate.operation(parent()))
    await entered.wait()
    maintenance = asyncio.create_task(gate.maintenance(asyncio.sleep(0)))
    await _until(lambda: gate.closing)
    go.set()
    await original
    assert not maintenance.done() and gate.active_operations == 1
    finish.set()
    await maintenance
    await asyncio.gather(*children)


@pytest.mark.asyncio
async def test_cancellable_spawn_holds_lease_until_real_cleanup_finishes():
    gate = DeviceOperationGate()
    started, cleanup, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def job():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleanup.set()
            await release.wait()

    task = gate.spawn(job(), cancellable=True)
    await started.wait()
    task.cancel()
    await cleanup.wait()
    assert gate.active_operations == 1
    maintenance = asyncio.create_task(gate.maintenance(asyncio.sleep(0)))
    await _until(lambda: gate.closing)
    assert not maintenance.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    await maintenance


@pytest.mark.asyncio
async def test_spawn_cancelled_before_scheduling_does_not_leak_a_reservation():
    gate = DeviceOperationGate()
    coro = asyncio.sleep(0)
    task = gate.spawn(coro, cancellable=True)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await _until(lambda: gate.active_operations == 0)
    assert inspect.getcoroutinestate(coro) == inspect.CORO_CLOSED


@pytest.mark.asyncio
async def test_detached_monitor_can_start_fresh_work_after_a_generation_change():
    gate = DeviceOperationGate()
    begin = asyncio.Event()
    workers = []

    async def monitor():
        await begin.wait()
        return await gate.operation(asyncio.sleep(0, result="fresh"))

    async def start():
        workers.append(gate.detached_task(monitor()))

    await gate.operation(start())
    await gate.maintenance(asyncio.sleep(0))
    begin.set()
    assert await workers[0] == "fresh"


@pytest.mark.asyncio
async def test_quiesce_precedes_drain_and_exclusive_body():
    gate = DeviceOperationGate()
    release = asyncio.Event()
    old = gate.spawn(release.wait())
    seen = []

    async def quiesce():
        assert gate.closing and gate.active_operations == 1
        seen.append("stop")
        release.set()

    async def erase():
        assert gate.closing and gate.active_operations == 0
        seen.append("erase")

    await gate.maintenance(erase(), quiesce=quiesce)
    await old
    assert seen == ["stop", "erase"]


@pytest.mark.asyncio
async def test_maintenance_is_single_flight_and_does_not_queue_another_erase():
    gate = DeviceOperationGate()
    inside, release = asyncio.Event(), asyncio.Event()

    async def erase():
        inside.set()
        await release.wait()

    first = asyncio.create_task(gate.maintenance(erase()))
    await inside.wait()
    with pytest.raises(ActionConflictError):
        await gate.maintenance(asyncio.sleep(0))
    release.set()
    await first


@pytest.mark.asyncio
async def test_optional_checkpoint_rejects_new_effects_during_drain():
    gate = DeviceOperationGate()
    entered, release = asyncio.Event(), asyncio.Event()

    async def old():
        entered.set()
        await release.wait()
        with pytest.raises(ActionConflictError):
            gate.checkpoint()

    task = asyncio.create_task(gate.operation(old()))
    await entered.wait()
    maintenance = asyncio.create_task(gate.maintenance(asyncio.sleep(0)))
    await _until(lambda: gate.closing)
    release.set()
    await asyncio.gather(task, maintenance)


@pytest.mark.asyncio
async def test_quiesce_cleanup_cannot_suppress_the_independent_drain_deadline():
    gate = DeviceOperationGate(timeout_seconds=0.02)
    release, started = asyncio.Event(), asyncio.Event()
    erased = False
    required = gate.spawn(release.wait())

    async def quiesce():
        started.set()
        await asyncio.gather(required)

    async def erase():
        nonlocal erased
        erased = True

    maintenance = asyncio.create_task(gate.maintenance(erase(), quiesce=quiesce))
    await started.wait()
    try:
        with pytest.raises(ActionConflictError):
            await asyncio.wait_for(maintenance, 0.3)
        assert gate.closing and gate.active_operations == 1
        assert not erased and not required.done()
        with pytest.raises(ActionConflictError):
            await gate.operation(asyncio.sleep(0))
    finally:
        release.set()
        await required
        await _until(lambda: not gate.closing)
    assert not erased


@pytest.mark.asyncio
async def test_cancelled_maintenance_caller_cannot_remove_the_drain_deadline():
    gate = DeviceOperationGate(timeout_seconds=0.02)
    release, started = asyncio.Event(), asyncio.Event()
    erased = False
    required = gate.spawn(release.wait())

    async def quiesce():
        started.set()
        await asyncio.gather(required)

    async def erase():
        nonlocal erased
        erased = True

    caller = asyncio.create_task(gate.maintenance(erase(), quiesce=quiesce))
    await started.wait()
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    # The timer belongs to the gate, not the departed request task.
    await asyncio.sleep(0.04)
    release.set()
    await required
    await _until(lambda: not gate.closing)
    assert not erased


@pytest.mark.asyncio
async def test_elapsed_quiesce_deadline_cannot_be_cancelled_before_due_timer_callback_runs():
    gate = DeviceOperationGate(timeout_seconds=.005)
    erased = False

    async def quiesce():
        # Simulate a short synchronous filesystem/loop stall. The due timer
        # cannot run until this coroutine yields, so absolute time must win.
        time.sleep(.025)

    async def erase():
        nonlocal erased
        erased = True

    with gate.request_scope():
        with pytest.raises(ActionConflictError):
            await gate.maintenance(erase(), quiesce=quiesce)
        assert not erased and not gate.closing and gate.active_operations == 0
        with pytest.raises(ActionConflictError):
            await gate.operation(asyncio.sleep(0))
    assert await gate.operation(asyncio.sleep(0, result="fresh")) == "fresh"


@pytest.mark.asyncio
async def test_changed_wakeup_after_deadline_cannot_enter_exclusive_body_without_another_await(monkeypatch):
    gate = DeviceOperationGate(timeout_seconds=.005)
    started, release = asyncio.Event(), asyncio.Event()
    erased = False

    async def work():
        started.set()
        await release.wait()

    old = asyncio.create_task(gate.operation(work()))
    await started.wait()

    async def late_wakeup():
        # Settle the lease first, then simulate returning from a changed-event
        # wakeup after the deadline but before the timer gets its loop turn.
        release.set()
        await old
        time.sleep(.025)

    monkeypatch.setattr(gate._changed, "wait", late_wakeup)

    async def erase():
        nonlocal erased
        erased = True

    with gate.request_scope():
        with pytest.raises(ActionConflictError):
            await gate.maintenance(erase())
        assert not erased and not gate.closing and gate.active_operations == 0
        with pytest.raises(ActionConflictError):
            await gate.operation(asyncio.sleep(0))
    assert await gate.operation(asyncio.sleep(0, result="fresh")) == "fresh"


@pytest.mark.parametrize("value", [0, -1, True, "5", float("inf"), float("nan")])
def test_invalid_drain_timeout_is_rejected(value):
    with pytest.raises(ValueError):
        DeviceOperationGate(timeout_seconds=value)
