"""Product gate integration: synthetic adapter, temporary DB/files only."""

import asyncio
import threading

import pytest

from ai_cat_controller.adapters.mock import MockAiCatAdapter
from ai_cat_controller.core.config import Settings
from ai_cat_controller.core.errors import ActionConflictError, ResourceNotFoundError
from ai_cat_controller.core.operation_gate import DeviceOperationGate
from ai_cat_controller.persistence.sqlite_repository import SQLiteRepository
from ai_cat_controller.services.motion_service import MotionService
from ai_cat_controller.services.personality_service import PersonalityService
from ai_cat_controller.services.product_mock_service import ProductMockService

SERIAL = "K1-GATE-SYNTHETIC"


async def wait_closing(gate):
    async def observed():
        while not gate.closing:
            await asyncio.sleep(.001)
    await asyncio.wait_for(observed(), 1)


def context(tmp_path, *, gate_timeout=1.0, **settings_kwargs):
    serial = tmp_path / "serial"
    serial.write_text(SERIAL)
    values = dict(data_path=tmp_path / "product.db", personality_runtime_path=tmp_path / "runtime.json",
        dialog_event_path=tmp_path / "events.jsonl", dialog_config_path=tmp_path / "dialog.json",
        dialog_text_request_path=tmp_path / "text.json", device_serial_path=serial,
        enable_product_data_reset=True, enable_touch_speech=True, touch_speech_cooldown_seconds=.5,
        debug_unlock_all_actions=True, motion_cooldown_seconds=0)
    values.update(settings_kwargs)
    settings = Settings(**values)
    repository = SQLiteRepository(settings.data_path)
    repository.initialize()
    user = repository.login_user("gate-owner", "合成用户")
    pet, _ = repository.bind_pet(user_id=user["user_id"], serial_number=SERIAL,
        pet_name="小安", network_name="Synthetic WiFi", personality_id="gentle_companion")
    gate = DeviceOperationGate(timeout_seconds=gate_timeout)
    adapter = MockAiCatAdapter(())
    motion = MotionService(adapter, command_timeout_seconds=1, cooldown_seconds=0)
    personality = PersonalityService(settings)
    product = ProductMockService(repository, motion, settings, personality, adapter, maintenance_gate=gate)
    return product, gate, repository, adapter, motion, user, pet


def command(product, name, user, pet):
    owner, pid = user["user_id"], pet["pet_id"]
    calls = {
        "login": lambda: product.login("new-user", "新用户"),
        "bind": lambda: product.bind_pet(user_id=owner, serial_number=SERIAL, pet_name="名字", network_name="WiFi"),
        "debug": lambda: product.debug_growth(user_id=owner, pet_id=pid, request_id="debug", event_type="question", count=1, topic="test", emotion="neutral", engagement=1),
        "interaction": lambda: product.add_interaction(user_id=owner, pet_id=pid, event_type="touch", request_id="touch", metadata={}),
        "action": lambda: product.execute_action(user_id=owner, pet_id=pid, action_id="head_nod", request_id="action"),
        "dialog": lambda: product.send_dialog(user_id=owner, pet_id=pid, content="合成问题", trigger_action=False),
        "settings": lambda: product.update_settings(user_id=owner, pet_id=pid, name="名字", volume=25),
        "touch": lambda: product.add_device_touch(device_serial=SERIAL, request_id="physical", metadata={"sensor": "head"}),
        "feedback": lambda: product.feedback(user_id=owner, pet_id=pid, category="other", content="合成反馈"),
        "device": lambda: product.update_mock_device_status(user_id=owner, pet_id=pid, online=True, battery_percent=50, charging=False, network_status="online"),
        "sync": lambda: product._sync_personality(pet),
        "monitor": lambda: product._sync_bound_native_dialog_events(),
        "native": lambda: product._sync_native_dialog_events(owner, pid),
        "history": lambda: product.dialog_history(owner, pid),
        "conversations": lambda: product.dialog_conversations(owner, pid),
        "detail": lambda: product.dialog_conversation(owner, pid, "native-test"),
    }
    return calls[name]()


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["login", "bind", "debug", "interaction", "action", "dialog", "settings", "touch",
    "feedback", "device", "sync", "monitor", "native", "history", "conversations", "detail"])
async def test_maintenance_rejects_every_product_write_or_import_before_any_side_effect(tmp_path, name):
    product, gate, repository, adapter, _, user, pet = context(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    async def exclusive():
        entered.set()
        await release.wait()
    task = asyncio.create_task(gate.maintenance(exclusive()))
    await entered.wait()
    with repository._connect() as connection:
        before = "\n".join(connection.iterdump())
    try:
        with pytest.raises(ActionConflictError):
            await command(product, name, user, pet)
        with repository._connect() as connection:
            assert "\n".join(connection.iterdump()) == before
        assert adapter.output_volume_percent == 60 and adapter.action_count == 0
        assert not product._finalizers and not product._sessions
    finally:
        release.set()
        await task


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_caller", [False, True])
async def test_unbind_waits_for_full_admitted_volume_and_sync_even_if_http_caller_cancelled(tmp_path, cancel_caller):
    product, gate, repository, adapter, _, user, pet = context(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    original = adapter.set_output_volume
    async def delayed(volume):
        entered.set()
        await release.wait()
        await original(volume)
    adapter.set_output_volume = delayed
    write = asyncio.create_task(product.update_settings(user_id=user["user_id"], pet_id=pet["pet_id"], name="保存名字", volume=35))
    await entered.wait()
    if cancel_caller:
        write.cancel()
        with pytest.raises(asyncio.CancelledError):
            await write
    maintenance = asyncio.create_task(product.unbind(user["user_id"], pet["pet_id"]))
    await wait_closing(gate)
    assert gate.closing and not maintenance.done()
    assert repository.get_owned_pet(user["user_id"], pet["pet_id"])["volume"] == 60
    with pytest.raises(ActionConflictError):
        await product.feedback(user_id=user["user_id"], pet_id=pet["pet_id"], category="other", content="不能越过维护")
    release.set()
    if not cancel_caller:
        await write
    await maintenance
    current = repository.get_pet_by_serial(SERIAL)
    assert current["owner_user_id"] is None and current["volume"] == 35
    assert adapter.output_volume_percent == 35 and not (await product._personality.status())["active"]
    assert gate.active_operations == 0


@pytest.mark.asyncio
async def test_reset_stops_motion_then_drains_product_finalizer_before_deleting_rows(tmp_path, monkeypatch):
    product, gate, repository, _, _, user, pet = context(tmp_path)
    events = []
    update, clear = repository.update_action_execution, repository.clear_product_data
    def updated(*args, **kwargs):
        events.append("settled")
        return update(*args, **kwargs)
    def cleared():
        events.append("clear")
        return clear()
    monkeypatch.setattr(repository, "update_action_execution", updated)
    monkeypatch.setattr(repository, "clear_product_data", cleared)
    result = await product.execute_action(user_id=user["user_id"], pet_id=pet["pet_id"], action_id="head_nod", request_id="motor")
    assert result["status"] == "running" and product._finalizers
    assert (await product.reset_product_data(user_id=user["user_id"]))["reset"]
    assert events[-1] == "clear" and "settled" in events[:-1]
    assert gate.active_operations == 0
    with repository._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM action_executions").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_reserved_touch_task_cannot_play_after_maintenance_advances_epoch(tmp_path, monkeypatch):
    product, gate, _, _, _, user, pet = context(tmp_path)
    plays = []
    monkeypatch.setattr(product._touch_phrase_player, "play_touch", lambda sensor: plays.append(sensor))
    await product._touch_speech_lock.acquire()
    async def schedule():
        assert product._schedule_touch_speech("head", "queued")
    await gate.operation(schedule())
    maintenance = asyncio.create_task(product.clear_dialog_memory(user_id=user["user_id"], pet_id=pet["pet_id"], request_id="clear"))
    await wait_closing(gate)
    assert gate.closing and not maintenance.done()
    product._touch_speech_lock.release()
    await maintenance
    assert plays == [] and gate.active_operations == 0


@pytest.mark.asyncio
async def test_optional_playback_thread_must_actually_settle_before_reset(tmp_path, monkeypatch):
    product, gate, repository, _, _, user, _ = context(tmp_path)
    started, release = threading.Event(), threading.Event()
    events = []
    def play(_):
        started.set()
        assert release.wait(2)
        events.append("audio_settled")
    clear = repository.clear_product_data
    def cleared():
        events.append("clear")
        return clear()
    monkeypatch.setattr(product._touch_phrase_player, "play_touch", play)
    monkeypatch.setattr(repository, "clear_product_data", cleared)
    async def schedule():
        assert product._schedule_touch_speech("head", "playing")
    await gate.operation(schedule())
    assert await asyncio.to_thread(started.wait, 1)
    maintenance = asyncio.create_task(product.reset_product_data(user_id=user["user_id"]))
    await asyncio.sleep(.01)
    assert not maintenance.done() and gate.active_operations > 0 and events == []
    release.set()
    await maintenance
    assert events == ["audio_settled", "clear"] and gate.active_operations == 0


@pytest.mark.asyncio
async def test_late_completed_action_still_records_result_but_does_not_start_old_event_speech(tmp_path, monkeypatch):
    product, gate, repository, _, motion, user, pet = context(tmp_path)
    row, _ = repository.create_action_execution(user_id=user["user_id"], pet_id=pet["pet_id"], action_id="head_nod", request_id="done")
    entered, release = asyncio.Event(), asyncio.Event()
    plays = []
    async def completed(_):
        entered.set()
        await release.wait()
        return "completed"
    monkeypatch.setattr(motion, "await_execution", completed)
    monkeypatch.setattr(product._touch_phrase_player, "play_event", lambda event: plays.append(event))
    finalizer = gate.spawn(product._finalize_action(row["execution_id"], "token", "level_up"), name="synthetic-finalizer")
    await entered.wait()
    maintenance = asyncio.create_task(product.clear_dialog_memory(user_id=user["user_id"], pet_id=pet["pet_id"], request_id="clear"))
    await wait_closing(gate)
    release.set()
    await asyncio.gather(finalizer, maintenance)
    assert not plays
    rows = repository.list_action_executions(user["user_id"], pet["pet_id"], 20)
    assert rows[0]["status"] == "completed" and gate.active_operations == 0


@pytest.mark.asyncio
async def test_native_monitor_reenters_fresh_scope_after_startup_and_maintenance(tmp_path, monkeypatch):
    from ai_cat_controller.services import product_mock_service
    product, gate, _, _, _, _, _ = context(tmp_path, hardware_driver="local_k1", api_key_enabled=True, api_key="synthetic")
    epochs = []
    async def record():
        async def body():
            epochs.append(gate.epoch)
        return await gate.operation(body())
    monkeypatch.setattr(product, "_sync_bound_native_dialog_events", record)
    monkeypatch.setattr(product_mock_service, "DIALOG_MEMORY_POLL_SECONDS", .005)
    await product.initialize()
    for _ in range(30):
        if len(epochs) >= 2:
            break
        await asyncio.sleep(.005)
    async def exclusive():
        return None
    await gate.maintenance(exclusive())
    for _ in range(30):
        if epochs and epochs[-1] == gate.epoch:
            break
        await asyncio.sleep(.005)
    await product.shutdown()
    assert len(epochs) >= 3 and epochs[-1] == gate.epoch and gate.active_operations == 0


@pytest.mark.asyncio
async def test_short_drain_timeout_does_not_unbind_or_clear_data_while_old_write_is_alive(tmp_path):
    product, gate, repository, adapter, _, user, pet = context(tmp_path, gate_timeout=.02)
    entered, release = asyncio.Event(), asyncio.Event()
    async def delayed(_):
        entered.set()
        await release.wait()
    adapter.set_output_volume = delayed
    write = asyncio.create_task(product.update_settings(user_id=user["user_id"], pet_id=pet["pet_id"], name=None, volume=35))
    await entered.wait()
    with pytest.raises(ActionConflictError):
        await product.unbind(user["user_id"], pet["pet_id"])
    assert repository.get_owned_pet(user["user_id"], pet["pet_id"])["volume"] == 60
    assert not gate.closing and gate.active_operations > 0
    release.set()
    await write
    assert repository.get_owned_pet(user["user_id"], pet["pet_id"])["volume"] == 35
    assert gate.active_operations == 0


@pytest.mark.asyncio
async def test_reset_quiesce_failure_does_not_create_backup_or_delete_any_data(tmp_path, monkeypatch):
    product, gate, repository, _, motion, user, pet = context(tmp_path)
    async def failed_stop():
        raise RuntimeError("synthetic stop failure")
    monkeypatch.setattr(motion, "stop", failed_stop)
    with pytest.raises(RuntimeError, match="synthetic"):
        await product.reset_product_data(user_id=user["user_id"])
    assert repository.get_owned_pet(user["user_id"], pet["pet_id"])
    assert not (tmp_path / "backups").exists() and not gate.closing and gate.active_operations == 0


@pytest.mark.asyncio
async def test_cancelled_maintenance_caller_does_not_abandon_already_admitted_unbind(tmp_path):
    product, gate, repository, adapter, _, user, pet = context(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    async def delayed(_):
        entered.set()
        await release.wait()
    adapter.set_output_volume = delayed
    write = asyncio.create_task(product.update_settings(user_id=user["user_id"], pet_id=pet["pet_id"], name=None, volume=35))
    await entered.wait()
    maintenance = asyncio.create_task(product.unbind(user["user_id"], pet["pet_id"]))
    await wait_closing(gate)
    assert gate.closing
    maintenance.cancel()
    with pytest.raises(asyncio.CancelledError):
        await maintenance
    assert gate.closing and repository.get_owned_pet(user["user_id"], pet["pet_id"])
    release.set()
    await write
    await gate.shutdown()
    assert repository.get_pet_by_serial(SERIAL)["owner_user_id"] is None and gate.active_operations == 0


@pytest.mark.asyncio
async def test_old_unreserved_child_cannot_schedule_effects_after_parent_and_maintenance_end(tmp_path):
    product, gate, _, _, _, user, pet = context(tmp_path)
    release = asyncio.Event()
    children = []
    async def late():
        await release.wait()
        return product._schedule_touch_speech("head", "expired")
    async def parent():
        children.append(asyncio.create_task(late()))
    await gate.operation(parent())
    await product.clear_dialog_memory(user_id=user["user_id"], pet_id=pet["pet_id"], request_id="clear")
    release.set()
    assert await children[0] is False and not product._finalizers and gate.active_operations == 0


@pytest.mark.asyncio
async def test_reserved_celebration_cannot_start_new_motion_after_epoch_changes(tmp_path):
    product, gate, repository, _, _, user, pet = context(tmp_path)
    await product._level_up_celebration_lock.acquire()
    async def schedule():
        assert product._schedule_level_up_celebration(user_id=user["user_id"], pet_id=pet["pet_id"],
            interaction_event_id="synthetic", to_level=1)
    await gate.operation(schedule())
    maintenance = asyncio.create_task(product.clear_dialog_memory(user_id=user["user_id"], pet_id=pet["pet_id"], request_id="clear"))
    await wait_closing(gate)
    assert gate.closing
    product._level_up_celebration_lock.release()
    await maintenance
    assert repository.list_action_executions(user["user_id"], pet["pet_id"], 20) == []
    assert gate.active_operations == 0


@pytest.mark.asyncio
async def test_required_finalizer_can_be_reserved_from_admitted_action_during_drain(tmp_path, monkeypatch):
    product, gate, repository, _, motion, user, pet = context(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    async def run(**kwargs):
        entered.set()
        await release.wait()
        return {"execution_token": "synthetic"}
    async def completed(_):
        return "completed"
    monkeypatch.setattr(motion, "run_sequence", run)
    monkeypatch.setattr(motion, "await_execution", completed)
    action = asyncio.create_task(product.execute_action(user_id=user["user_id"], pet_id=pet["pet_id"], action_id="head_nod", request_id="one"))
    await entered.wait()
    maintenance = asyncio.create_task(product.clear_dialog_memory(user_id=user["user_id"], pet_id=pet["pet_id"], request_id="clear"))
    await wait_closing(gate)
    assert gate.closing
    release.set()
    await asyncio.gather(action, maintenance)
    rows = repository.list_action_executions(user["user_id"], pet["pet_id"], 20)
    assert rows[0]["status"] == "completed" and gate.active_operations == 0


@pytest.mark.asyncio
async def test_cancelling_optional_celebration_during_execution_insert_keeps_commit_lease_until_settled(tmp_path, monkeypatch):
    product, gate, repository, _, motion, user, pet = context(tmp_path, gate_timeout=3)
    entered, release = threading.Event(), threading.Event()
    events = []
    create, clear = repository.create_action_execution, repository.clear_product_data
    def inserting(**kwargs):
        result = create(**kwargs)
        entered.set()
        assert release.wait(2)
        events.append("insert_settled")
        return result
    def clearing():
        events.append("clear")
        return clear()
    async def run(**kwargs):
        return {"execution_token": "synthetic"}
    async def completed(_):
        return "completed"
    monkeypatch.setattr(repository, "create_action_execution", inserting)
    monkeypatch.setattr(repository, "clear_product_data", clearing)
    monkeypatch.setattr(motion, "run_sequence", run)
    monkeypatch.setattr(motion, "await_execution", completed)
    async def schedule():
        assert product._schedule_level_up_celebration(user_id=user["user_id"], pet_id=pet["pet_id"],
            interaction_event_id="inserting", to_level=1)
    await gate.operation(schedule())
    assert await asyncio.to_thread(entered.wait, 1)
    maintenance = asyncio.create_task(product.reset_product_data(user_id=user["user_id"]))
    await asyncio.sleep(.01)
    assert gate.closing and not maintenance.done() and events == []
    release.set()
    await maintenance
    assert events == ["insert_settled", "clear"] and gate.active_operations == 0
    with repository._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM action_executions").fetchone()[0] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["clear", "unbind"])
@pytest.mark.parametrize("invalid", ["owner", "pet"])
async def test_unowned_lifecycle_request_cannot_advance_epoch_or_invalidate_old_ticket(tmp_path, monkeypatch, kind, invalid):
    product, gate, repository, adapter, _, user, pet = context(tmp_path)
    calls, reads = [], []
    status, owned = adapter.get_dialog_status, repository.get_owned_pet
    async def observed_status():
        calls.append("status")
        return await status()
    def observed_owned(*args, **kwargs):
        reads.append(kwargs.get("read_only", False))
        return owned(*args, **kwargs)
    monkeypatch.setattr(adapter, "get_dialog_status", observed_status)
    monkeypatch.setattr(repository, "get_owned_pet", observed_owned)
    monkeypatch.setattr(product._touch_phrase_player, "play_touch", lambda _: calls.append("speech"))
    target_user = "usr_wrong_owner" if invalid == "owner" else user["user_id"]
    target_pet = "pet_missing" if invalid == "pet" else pet["pet_id"]
    before = gate.epoch
    with gate.request_scope():
        with pytest.raises(ResourceNotFoundError):
            if kind == "clear":
                await product.clear_dialog_memory(user_id=target_user, pet_id=target_pet, request_id="denied-clear")
            else:
                await product.unbind(target_user, target_pet)
        assert gate.epoch == before and not gate.closing and gate.active_operations == 0
        assert calls == [] and reads == [True]
        # Rejection must not poison a previously admitted ingress ticket.
        result = await product.feedback(user_id=user["user_id"], pet_id=pet["pet_id"], category="other", content="原票据仍然有效")
        assert result["category"] == "other"
    assert not (tmp_path / "backups").exists() and not product._finalizers
    with repository._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM dialog_memory_state").fetchone()[0] == 0
        assert connection.execute("SELECT owner_user_id FROM pets WHERE pet_id=?", (pet["pet_id"],)).fetchone()[0] == user["user_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["clear", "unbind"])
async def test_owner_change_after_readonly_preflight_is_rejected_again_under_lifecycle_lock(tmp_path, monkeypatch, kind):
    product, gate, repository, _, _, user, pet = context(tmp_path)
    replacement = repository.login_user("replacement-after-preflight", "新主人")
    owned, once = repository.get_owned_pet, [False]
    def transfer_after_read(*args, **kwargs):
        result = owned(*args, **kwargs)
        if kwargs.get("read_only") is True and not once[0]:
            once[0] = True
            repository.unbind_pet(user["user_id"], pet["pet_id"])
            repository.bind_pet(user_id=replacement["user_id"], serial_number=SERIAL,
                pet_name="新主名字", network_name="Synthetic WiFi", personality_id="gentle_companion")
        return result
    monkeypatch.setattr(repository, "get_owned_pet", transfer_after_read)
    with pytest.raises(ResourceNotFoundError):
        if kind == "clear":
            await product.clear_dialog_memory(user_id=user["user_id"], pet_id=pet["pet_id"], request_id="stale-owner")
        else:
            await product.unbind(user["user_id"], pet["pet_id"])
    assert once[0] and not gate.closing and gate.active_operations == 0
    assert repository.get_pet_by_serial(SERIAL)["owner_user_id"] == replacement["user_id"]
    assert not (tmp_path / "backups").exists()
    with repository._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM dialog_memory_state").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_clear_rechecks_owner_before_observing_replacement_owners_busy_voice(tmp_path, monkeypatch):
    product, gate, repository, adapter, _, user, pet = context(tmp_path)
    replacement = repository.login_user("busy-replacement", "新主人")
    owned, transferred, status_calls = repository.get_owned_pet, [False], []
    def transfer_after_preflight(*args, **kwargs):
        result = owned(*args, **kwargs)
        if kwargs.get("read_only") is True and not transferred[0]:
            transferred[0] = True
            repository.unbind_pet(user["user_id"], pet["pet_id"])
            repository.bind_pet(user_id=replacement["user_id"], serial_number=SERIAL,
                pet_name="新主名字", network_name="Synthetic WiFi", personality_id="gentle_companion")
        return result
    async def busy_status():
        status_calls.append("private-new-owner-state")
        return {"state": "thinking", "session_active": True, "stale": False}
    monkeypatch.setattr(repository, "get_owned_pet", transfer_after_preflight)
    monkeypatch.setattr(adapter, "get_dialog_status", busy_status)
    with pytest.raises(ResourceNotFoundError):
        await product.clear_dialog_memory(user_id=user["user_id"], pet_id=pet["pet_id"], request_id="stale-busy-owner")
    assert transferred[0] and status_calls == [] and not gate.closing
    assert repository.get_pet_by_serial(SERIAL)["owner_user_id"] == replacement["user_id"]
    with repository._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM dialog_memory_state").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_ungated_clear_also_rejects_nonowner_before_reading_private_voice_state(tmp_path, monkeypatch):
    product, _, repository, adapter, _, _, pet = context(tmp_path)
    product._maintenance_gate = None
    status_calls = []
    async def busy_status():
        status_calls.append("private-owner-state")
        return {"state": "thinking", "session_active": True, "stale": False}
    monkeypatch.setattr(adapter, "get_dialog_status", busy_status)
    with pytest.raises(ResourceNotFoundError):
        await product.clear_dialog_memory(user_id="usr_wrong_owner", pet_id=pet["pet_id"], request_id="wrong-owner")
    assert status_calls == []
    with repository._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM dialog_memory_state").fetchone()[0] == 0
