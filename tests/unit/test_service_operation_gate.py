"""Shared process gate at service entry, background work and safety-stop seams.

All effects are synthetic adapters, in-memory fakes and temporary config files.
"""

import asyncio
from threading import Event as ThreadEvent

import pytest

from ai_cat_controller.adapters.base import Capability
from ai_cat_controller.adapters.mock import MockAiCatAdapter
from ai_cat_controller.core.config import Settings
from ai_cat_controller.core.errors import ActionConflictError
from ai_cat_controller.core.operation_gate import DeviceOperationGate
from ai_cat_controller.services.device_service import DeviceService
from ai_cat_controller.services.dialog_service import DialogService
from ai_cat_controller.services.mijia_gateway_service import MijiaGatewayService
from ai_cat_controller.services.motion_service import MotionService


SERVICES = ("volc-pulseaudio.service", "volc-conv-ai.service", "volc-k1-wake-word.service")
UTC = "2026-10-21T00:00:00+00:00"


async def settled():
    for _ in range(20):
        await asyncio.sleep(0)


class Adapter(MockAiCatAdapter):
    capabilities = frozenset(Capability)

    def __init__(self):
        super().__init__(SERVICES)
        self.calls = []
        self.motion_started = asyncio.Event()
        self.motion_release = asyncio.Event()
        self.motion_cleanup_release = asyncio.Event()
        self.motion_cleanup_release.set()
        self.motion_cleanup = asyncio.Event()
        self.wake_started = asyncio.Event()
        self.wake_release = asyncio.Event()
        self.wake_cleanup = asyncio.Event()
        self.text_started = asyncio.Event()
        self.text_release = asyncio.Event()
        self.stop_started = asyncio.Event()
        self.stop_release = asyncio.Event()
        self.stop_release.set()

    async def nod_head(self, intensity, duration_ms):
        self.calls.append("motion")
        self.motion_started.set()
        try:
            await self.motion_release.wait()
        finally:
            await self.motion_cleanup_release.wait()
            self.motion_cleanup.set()

    async def stop_motion(self):
        self.calls.append("stop")
        self.stop_started.set()
        await self.stop_release.wait()
        return True

    async def wake_dialog(self):
        self.calls.append("wake")
        self.wake_started.set()
        try:
            await self.wake_release.wait()
        finally:
            self.wake_cleanup.set()

    async def interrupt_dialog(self):
        self.calls.append("interrupt")
        await super().interrupt_dialog()

    async def send_text_dialog(self, content, request_id):
        self.calls.append("text")
        self.text_started.set()
        await self.text_release.wait()

    async def speak_text(self, content, request_id):
        self.calls.append("speak")
        self.text_started.set()
        await self.text_release.wait()


class Repository:
    def __init__(self, trace):
        self.trace = trace
        self.context = {"user_id": "owner", "pet_id": "pet", "device_id": "device"}
        self.requests = {}

    def get_mijia_gateway_context(self, gateway_device_id):
        return dict(self.context)

    def get_mijia_gateway_request(self, gateway_device_id, request_id):
        return self.requests.get((gateway_device_id, request_id))

    def save_mijia_gateway_request(self, **values):
        self.trace.append("receipt")
        self.requests[(values["gateway_device_id"], values["request_id"])] = values

    def prepare_mijia_integration(self, **values):
        self.trace.append("prepare")
        return {"status": "prepared", "gateway_device_id": "gateway", "miot_model": None,
                "prepared_at": UTC, "updated_at": UTC}, True, False

    def remove_mijia_integration(self, **values):
        self.trace.append("remove")
        return {"status": "removed", "gateway_device_id": "gateway", "miot_model": None,
                "prepared_at": UTC, "updated_at": UTC}, True, False


class Product:
    has_tail = True

    def __init__(self, trace):
        self.trace = trace
        self.physical_started = asyncio.Event()
        self.physical_release = asyncio.Event()
        self.physical_release.set()

    async def update_settings(self, **values):
        self.trace.append("volume-physical")
        self.physical_started.set()
        await self.physical_release.wait()
        self.trace.append("volume-db")

    async def execute_action(self, **values):
        self.trace.append("product-action")
        return {"status": "accepted", "execution_id": "execution"}


def services(tmp_path):
    gate, adapter, trace = DeviceOperationGate(timeout_seconds=2), Adapter(), []
    dialog = DialogService(adapter, tmp_path / "config.json", maintenance_gate=gate)
    motion = MotionService(adapter, command_timeout_seconds=5, cooldown_seconds=0, maintenance_gate=gate)
    device = DeviceService(adapter, Settings(hardware_driver="mock"), motion, dialog, maintenance_gate=gate)
    repository, product = Repository(trace), Product(trace)
    mijia = MijiaGatewayService(repository, product, device, dialog, motion, maintenance_gate=gate)
    return gate, adapter, dialog, motion, device, repository, product, mijia, trace


async def begin_maintenance(gate, trace=None):
    entered, release = asyncio.Event(), asyncio.Event()

    async def body():
        if trace is not None:
            trace.append("maintenance")
        entered.set()
        await release.wait()

    task = asyncio.create_task(gate.maintenance(body()))
    await settled()
    return task, entered, release


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", [
    "config", "wake", "text", "speak", "nod", "shake", "tail", "sequence",
    "prepare", "remove", "volume", "mijia-motion", "mijia-listen",
])
async def test_maintenance_rejects_all_ordinary_service_writes_before_effects(tmp_path, operation):
    gate, adapter, dialog, motion, _, repository, _, mijia, trace = services(tmp_path)
    task, entered, release = await begin_maintenance(gate)
    assert entered.is_set()
    operations = {
        "config": lambda: dialog.update_config(60), "wake": lambda: dialog.wake("wake"),
        "text": lambda: dialog.send_text("synthetic text", "text"), "speak": lambda: dialog.speak("synthetic speech", "speak"),
        "nod": lambda: motion.nod_head(.1, 10, "nod"), "shake": lambda: motion.shake_head(.1, 10, "shake"),
        "tail": lambda: motion.wag_tail(.1, 10, "tail"),
        "sequence": lambda: motion.run_sequence(action_name="sequence", steps=((Capability.NOD_HEAD, .1, 10),), request_id="sequence"),
        "prepare": lambda: mijia.prepare("owner", "pet", "prepare"), "remove": lambda: mijia.remove("owner", "pet", "remove"),
        "volume": lambda: mijia.write_volume("gateway", value=50, request_id="volume"),
        "mijia-motion": lambda: mijia.invoke_action("gateway", "head_nod", "motion"),
        "mijia-listen": lambda: mijia.invoke_action("gateway", "start_listening", "listen"),
    }
    try:
        with pytest.raises(ActionConflictError):
            await operations[operation]()
        assert adapter.calls == [] and trace == [] and repository.requests == {}
        assert not (tmp_path / "config.json").exists()
    finally:
        release.set()
        await task


@pytest.mark.asyncio
async def test_pure_device_dialog_config_and_service_reads_do_not_wait_for_maintenance(tmp_path):
    gate, _, dialog, _, device, _, _, _, _ = services(tmp_path)
    task, entered, release = await begin_maintenance(gate)
    assert entered.is_set()
    try:
        status, config, hardware, local_services = await asyncio.wait_for(asyncio.gather(
            dialog.get_status(), dialog.get_config(), device.get_device_status(), device.get_services_status(),
        ), timeout=1)
        assert config["source"] == "default"
        assert isinstance(status["state"], str) and hardware["adapter_mode"] == "mock" and local_services
    finally:
        release.set()
        await task


@pytest.mark.asyncio
async def test_acknowledged_background_motion_keeps_lease_through_safe_stop_cleanup(tmp_path):
    gate, adapter, _, motion, _, _, _, _, _ = services(tmp_path)
    adapter.motion_cleanup_release.clear()
    response = await motion.nod_head(.1, 10, "accepted")
    await asyncio.wait_for(adapter.motion_started.wait(), 1)
    assert response["state"] == "running" and gate.active_operations > 0
    maintenance, entered, release = await begin_maintenance(gate)
    assert not entered.is_set()
    stop = asyncio.create_task(motion.stop())
    await settled()
    assert not entered.is_set() and not stop.done()
    adapter.motion_cleanup_release.set()
    assert (await stop)["stopped"] is True
    await asyncio.wait_for(entered.wait(), 1)
    assert adapter.motion_cleanup.is_set() and gate.active_operations == 0
    release.set()
    await maintenance
    adapter.motion_release.set()
    await settled()
    assert adapter.calls.count("motion") == 1 and motion.current_action == "idle"


@pytest.mark.asyncio
async def test_cancelled_motion_submitter_cannot_release_background_lease_before_effect_finishes(tmp_path):
    gate, adapter, _, motion, _, _, _, _, _ = services(tmp_path)
    await motion._lock.acquire()
    submit = asyncio.create_task(motion.nod_head(.1, 10, "cancelled-caller"))
    await settled()
    submit.cancel()
    with pytest.raises(asyncio.CancelledError):
        await submit
    maintenance, entered, release = await begin_maintenance(gate)
    assert not entered.is_set()
    motion._lock.release()
    await asyncio.wait_for(adapter.motion_started.wait(), 1)
    assert not entered.is_set()
    await motion.stop()
    await asyncio.wait_for(entered.wait(), 1)
    release.set()
    await maintenance
    assert adapter.calls.count("motion") == 1


@pytest.mark.asyncio
async def test_cancelled_wake_caller_keeps_lease_until_independent_interrupt_cancels_actual_wake(tmp_path):
    gate, adapter, dialog, _, _, _, _, _, _ = services(tmp_path)
    wake = asyncio.create_task(dialog.wake("cancelled-caller"))
    await asyncio.wait_for(adapter.wake_started.wait(), 1)
    wake.cancel()
    with pytest.raises(asyncio.CancelledError):
        await wake
    maintenance, entered, release = await begin_maintenance(gate)
    assert not entered.is_set()
    assert (await dialog.interrupt("safe-interrupt"))["changed"] is True
    await asyncio.wait_for(entered.wait(), 1)
    assert adapter.wake_cleanup.is_set() and adapter.calls.count("interrupt") == 1
    release.set()
    await maintenance


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["text", "speak"])
async def test_dialog_effect_is_retained_after_http_cancellation_until_actual_send_finishes(tmp_path, kind):
    gate, adapter, dialog, _, _, _, _, _, _ = services(tmp_path)
    send = asyncio.create_task((dialog.send_text if kind == "text" else dialog.speak)("synthetic body", kind))
    await asyncio.wait_for(adapter.text_started.wait(), 1)
    send.cancel()
    with pytest.raises(asyncio.CancelledError):
        await send
    maintenance, entered, release = await begin_maintenance(gate)
    assert not entered.is_set()
    adapter.text_release.set()
    await asyncio.wait_for(entered.wait(), 1)
    release.set()
    await maintenance
    assert adapter.calls.count(kind) == 1


@pytest.mark.asyncio
async def test_to_thread_config_write_keeps_lease_after_caller_cancels(tmp_path, monkeypatch):
    gate, _, dialog, _, _, _, _, _, _ = services(tmp_path)
    started, continue_write = ThreadEvent(), ThreadEvent()
    original = dialog._write_config

    def write(value):
        started.set()
        assert continue_write.wait(5)
        original(value)

    monkeypatch.setattr(dialog, "_write_config", write)
    request = asyncio.create_task(dialog.update_config(70))
    assert await asyncio.to_thread(started.wait, 1)
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    maintenance, entered, release = await begin_maintenance(gate)
    assert not entered.is_set()
    continue_write.set()
    await asyncio.wait_for(entered.wait(), 1)
    assert (await dialog.get_config())["follow_up_seconds"] == 70
    release.set()
    await maintenance


@pytest.mark.asyncio
async def test_mijia_volume_holds_one_scope_across_physical_effect_database_and_receipt(tmp_path):
    gate, _, _, _, _, repository, product, mijia, trace = services(tmp_path)
    product.physical_release.clear()
    request = asyncio.create_task(mijia.write_volume("gateway", value=45, request_id="volume"))
    await asyncio.wait_for(product.physical_started.wait(), 1)
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    maintenance, entered, release = await begin_maintenance(gate, trace)
    assert not entered.is_set()
    product.physical_release.set()
    await asyncio.wait_for(entered.wait(), 1)
    assert trace == ["volume-physical", "volume-db", "receipt", "maintenance"]
    assert repository.requests[("gateway", "volume")]["result"]["value"] == 45
    release.set()
    await maintenance


@pytest.mark.asyncio
async def test_mijia_safe_stop_does_not_queue_behind_ordinary_command_lock(tmp_path):
    _, adapter, _, _, _, repository, product, mijia, _ = services(tmp_path)
    product.physical_release.clear()
    volume = asyncio.create_task(mijia.write_volume("gateway", value=40, request_id="ordinary"))
    await asyncio.wait_for(product.physical_started.wait(), 1)
    result = await asyncio.wait_for(mijia.invoke_action("gateway", "stop_motion", "safe-stop"), 1)
    assert result["status"] == "completed" and adapter.calls.count("stop") == 1 and not volume.done()
    assert ("gateway", "safe-stop") in repository.requests
    product.physical_release.set()
    await volume
    duplicate = await mijia.invoke_action("gateway", "stop_motion", "safe-stop")
    assert duplicate["duplicate"] is True and adapter.calls.count("stop") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["stop_motion", "interrupt_dialog"])
async def test_mijia_safety_stop_executes_during_maintenance_without_inserting_old_receipt(tmp_path, action):
    gate, adapter, _, _, _, repository, _, mijia, _ = services(tmp_path)
    maintenance, entered, release = await begin_maintenance(gate)
    assert entered.is_set()
    try:
        with pytest.raises(ActionConflictError, match="停止/打断已请求但维护中未记录回执"):
            await mijia.invoke_action("gateway", action, "safety-no-receipt")
        assert adapter.calls.count("stop" if action == "stop_motion" else "interrupt") == 1
        assert repository.requests == {}
    finally:
        release.set()
        await maintenance


@pytest.mark.asyncio
async def test_mijia_safe_stop_spanning_completed_maintenance_cannot_reacquire_new_epoch_to_save(tmp_path):
    gate, adapter, _, _, _, repository, _, mijia, _ = services(tmp_path)
    adapter.stop_release.clear()
    request = asyncio.create_task(mijia.invoke_action("gateway", "stop_motion", "spanning-stop"))
    await asyncio.wait_for(adapter.stop_started.wait(), 1)
    maintenance, entered, release = await begin_maintenance(gate)
    assert entered.is_set()
    release.set()
    await maintenance
    adapter.stop_release.set()
    with pytest.raises(ActionConflictError, match="停止/打断已请求但维护中未记录回执"):
        await request
    assert repository.requests == {} and adapter.calls.count("stop") == 1


@pytest.mark.asyncio
async def test_mijia_old_ingress_ticket_still_stops_but_reports_unrecorded_receipt_honestly(tmp_path):
    gate, adapter, _, _, _, repository, _, mijia, _ = services(tmp_path)
    with gate.request_scope():
        async def maintenance_body():
            return None
        await gate.maintenance(maintenance_body())
        with pytest.raises(ActionConflictError, match="停止/打断已请求但维护中未记录回执"):
            await mijia.invoke_action("gateway", "stop_motion", "old-ingress")
    assert adapter.calls.count("stop") == 1 and repository.requests == {}


@pytest.mark.asyncio
async def test_stop_of_reserved_motion_before_execute_body_starts_settles_completion(tmp_path, monkeypatch):
    gate, _, _, motion, _, _, _, _, _ = services(tmp_path)
    original_spawn = gate.spawn
    release = asyncio.Event()

    def delayed_spawn(operation, **options):
        if str(options.get("name", "")).startswith("ai-cat-"):
            async def delayed():
                try:
                    await release.wait()
                    return await operation
                finally:
                    operation.close()
            return original_spawn(delayed(), **options)
        return original_spawn(operation, **options)

    monkeypatch.setattr(gate, "spawn", delayed_spawn)
    response = await motion.run_sequence(action_name="reserved", steps=((Capability.NOD_HEAD, .1, 10),), request_id="reserved")
    await motion.stop()
    assert await motion.await_execution(response["execution_token"], timeout=.1) == "cancelled"
    assert motion.current_action == "idle" and gate.active_operations == 0


@pytest.mark.asyncio
async def test_mijia_safe_receipt_rechecks_gateway_owner_after_physical_stop(tmp_path):
    _, adapter, _, _, _, repository, _, mijia, _ = services(tmp_path)
    adapter.stop_release.clear()
    request = asyncio.create_task(mijia.invoke_action("gateway", "stop_motion", "owner-changed"))
    await asyncio.wait_for(adapter.stop_started.wait(), 1)
    repository.context["user_id"] = "different-owner"
    adapter.stop_release.set()
    with pytest.raises(ActionConflictError, match="归属已变化"):
        await request
    assert repository.requests == {}
