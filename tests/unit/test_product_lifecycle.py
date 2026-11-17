"""Lifecycle regressions use MockAdapter and temporary files only."""

import asyncio
import json
import threading

import pytest

from ai_cat_controller.adapters.mock import MockAiCatAdapter
from ai_cat_controller.core.config import Settings
from ai_cat_controller.core.errors import ActionConflictError
from ai_cat_controller.persistence.sqlite_repository import SQLiteRepository
from ai_cat_controller.services.motion_service import MotionService
from ai_cat_controller.services.personality_service import PersonalityService
from ai_cat_controller.services.product_mock_service import ProductMockService

SERIAL = "K1-LIFECYCLE-SYNTHETIC"


def context(tmp_path, *, existing=True):
    serial = tmp_path / "serial"
    serial.write_text(SERIAL)
    settings = Settings(hardware_driver="local_k1", api_key_enabled=True, api_key="synthetic-key",
        device_serial_path=serial, personality_runtime_path=tmp_path / "personality.json",
        dialog_event_path=tmp_path / "events.jsonl", dialog_text_request_path=tmp_path / "text.json",
        dialog_config_path=tmp_path / "dialog.json", data_path=tmp_path / "product.db",
        enable_product_data_reset=True, enable_touch_speech=False, enable_level_up_celebration=False)
    repository = SQLiteRepository(settings.data_path)
    repository.initialize()
    user = repository.login_user("synthetic-owner", "用户")
    pet = None
    if existing:
        pet, _ = repository.bind_pet(user_id=user["user_id"], serial_number=SERIAL,
            pet_name="旧主名字", network_name="Synthetic WiFi", personality_id="gentle_companion")
        repository.add_dialog_pair(user_id=user["user_id"], pet_id=pet["pet_id"],
            user_content="我叫小雨，我喜欢蓝色", assistant_content="合成回答", voice_id="synthetic")
    adapter = MockAiCatAdapter(())
    motion = MotionService(adapter, command_timeout_seconds=1, cooldown_seconds=0)
    personality = PersonalityService(settings)
    product = ProductMockService(repository, motion, settings, personality, adapter)
    return product, personality, repository, adapter, settings, user, pet


@pytest.mark.asyncio
async def test_unbind_immediately_removes_private_runtime_but_preserves_product_history(tmp_path):
    product, personality, repository, _, settings, user, pet = context(tmp_path)
    await product._sync_personality(pet)
    assert "小雨" in settings.personality_runtime_path.read_text()
    await product.unbind(user["user_id"], pet["pet_id"])
    assert not settings.personality_runtime_path.exists()
    assert (await personality.status())["active"] is False
    assert repository.get_pet_by_serial(SERIAL)["owner_user_id"] is None
    with repository._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM dialog_history").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM dialog_long_term_memories").fetchone()[0] > 0


@pytest.mark.asyncio
@pytest.mark.parametrize("state,active,stale", [("listening", True, False), ("thinking", False, False),
    ("ready", False, True), ("unavailable", False, True)])
async def test_unbind_rejects_active_or_unverified_voice_without_changes(tmp_path, state, active, stale):
    product, _, repository, adapter, settings, user, pet = context(tmp_path)
    await product._sync_personality(pet)
    before = settings.personality_runtime_path.read_bytes()
    async def status():
        return {"state": state, "session_active": active, "stale": stale}
    adapter.get_dialog_status = status
    with pytest.raises(ActionConflictError):
        await product.unbind(user["user_id"], pet["pet_id"])
    assert repository.get_owned_pet(user["user_id"], pet["pet_id"])
    assert settings.personality_runtime_path.read_bytes() == before


@pytest.mark.asyncio
async def test_old_owner_late_sync_neither_recreates_runtime_nor_exposes_new_owner(tmp_path):
    product, _, repository, _, settings, user, pet = context(tmp_path)
    await product._sync_personality(pet)
    await product.unbind(user["user_id"], pet["pet_id"])
    late = await product._sync_personality(pet)
    assert late == {"sync_state": "superseded"} and not settings.personality_runtime_path.exists()
    new_user = repository.login_user("another-owner", "新用户")
    await product.bind_pet(user_id=new_user["user_id"], serial_number=SERIAL,
        pet_name="新主名字", network_name="Synthetic WiFi")
    before = settings.personality_runtime_path.read_bytes()
    assert await product._sync_personality(pet) == {"sync_state": "superseded"}
    assert settings.personality_runtime_path.read_bytes() == before
    assert "小雨" not in before.decode() and "新主名字" in before.decode()


@pytest.mark.asyncio
async def test_same_owner_rebinding_still_invalidates_old_binding_snapshot(tmp_path):
    product, _, _, _, settings, user, pet = context(tmp_path)
    await product._sync_personality(pet)
    await product.unbind(user["user_id"], pet["pet_id"])
    await product.bind_pet(user_id=user["user_id"], serial_number=SERIAL,
        pet_name="重新绑定名字", network_name="Synthetic WiFi")
    before = settings.personality_runtime_path.read_bytes()
    assert await product._sync_personality(pet) == {"sync_state": "superseded"}
    assert settings.personality_runtime_path.read_bytes() == before


@pytest.mark.asyncio
async def test_rebinding_waits_for_old_owner_runtime_invalidation(tmp_path, monkeypatch):
    product, personality, repository, _, settings, user, pet = context(tmp_path)
    await product._sync_personality(pet)
    entered, release = asyncio.Event(), asyncio.Event()
    original = personality.clear_runtime
    async def delayed_clear():
        entered.set()
        await release.wait()
        await original()
    monkeypatch.setattr(personality, "clear_runtime", delayed_clear)
    unbind = asyncio.create_task(product.unbind(user["user_id"], pet["pet_id"]))
    await entered.wait()
    new_user = repository.login_user("replacement", "新主人")
    bind = asyncio.create_task(product.bind_pet(user_id=new_user["user_id"], serial_number=SERIAL,
        pet_name="新名字", network_name="Synthetic WiFi"))
    await asyncio.sleep(.01)
    assert not bind.done() and repository.get_pet_by_serial(SERIAL)["owner_user_id"] is None
    release.set()
    await asyncio.wait_for(asyncio.gather(unbind, bind), 2)
    payload = json.loads(settings.personality_runtime_path.read_text())
    assert payload["pet_name"] == "新名字" and payload["memory_message_count"] == 0


@pytest.mark.asyncio
async def test_unbinding_other_repository_device_does_not_clear_this_physical_runtime(tmp_path):
    product, _, repository, _, settings, user, pet = context(tmp_path)
    await product._sync_personality(pet)
    other, _ = repository.bind_pet(user_id=user["user_id"], serial_number="K1-OTHER-SYNTHETIC",
        pet_name="另一设备", network_name="Synthetic WiFi", personality_id="proud_star")
    before = settings.personality_runtime_path.read_bytes()
    await product.unbind(user["user_id"], other["pet_id"])
    assert settings.personality_runtime_path.read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", [True, False])
async def test_restart_without_bound_local_owner_removes_stale_disk_runtime_even_with_empty_cache(tmp_path, existing):
    product, personality, repository, _, settings, user, pet = context(tmp_path, existing=existing)
    if existing:
        repository.unbind_pet(user["user_id"], pet["pet_id"])
    settings.personality_runtime_path.write_text('{"oldOwnerMemory":"private stale data"}')
    assert (await personality.status())["active"] is False
    await product._initialize_personality()
    assert not settings.personality_runtime_path.exists()


@pytest.mark.asyncio
async def test_memory_clear_waits_for_inflight_sync_and_late_sync_reloads_current_cutoff(tmp_path):
    product, personality, _, _, settings, user, pet = context(tmp_path)
    await product._sync_personality(pet)
    entered, release = asyncio.Event(), asyncio.Event()
    original = personality.sync_pet
    async def deferred(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)
    personality.sync_pet = deferred
    old = asyncio.create_task(product._sync_personality(pet))
    await entered.wait()
    clear = asyncio.create_task(product.clear_dialog_memory(user_id=user["user_id"], pet_id=pet["pet_id"], request_id="clear-synthetic"))
    await asyncio.sleep(0)
    release.set()
    await asyncio.wait_for(asyncio.gather(old, clear), 2)
    assert json.loads(settings.personality_runtime_path.read_text())["memory_message_count"] == 0
    await product._sync_personality(pet)
    payload = json.loads(settings.personality_runtime_path.read_text())
    assert payload["memory_message_count"] == 0 and payload["long_term_memory_count"] == 0
    assert "小雨" not in payload["system_prompt"]


@pytest.mark.asyncio
async def test_personality_sync_started_before_clear_cannot_write_after_generation_changes(tmp_path, monkeypatch):
    _, personality, _, _, settings, _, pet = context(tmp_path)
    entered, release = threading.Event(), threading.Event()
    def serial(_):
        entered.set()
        assert release.wait(2)
        return SERIAL
    monkeypatch.setattr(personality, "read_device_serial", serial)
    sync = asyncio.create_task(personality.sync_pet(pet, dialog_memory=[{"role": "user", "content": "旧主秘密"}]))
    assert await asyncio.to_thread(entered.wait, 1)
    await personality.clear_runtime()
    release.set()
    assert await sync == {"sync_state": "superseded"}
    assert not settings.personality_runtime_path.exists() and not (await personality.status())["active"]


@pytest.mark.asyncio
async def test_cancelled_sync_cannot_release_lifecycle_gate_before_disk_write_settles(tmp_path, monkeypatch):
    product, personality, _, _, settings, user, pet = context(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = personality._write_profile
    def delayed(profile):
        entered.set()
        assert release.wait(2)
        original(profile)
    monkeypatch.setattr(personality, "_write_profile", delayed)
    sync = asyncio.create_task(product._sync_personality(pet))
    assert await asyncio.to_thread(entered.wait, 1)
    sync.cancel()
    unbind = asyncio.create_task(product.unbind(user["user_id"], pet["pet_id"]))
    await asyncio.sleep(.01)
    assert not unbind.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await sync
    await asyncio.wait_for(unbind, 2)
    assert not settings.personality_runtime_path.exists()


@pytest.mark.asyncio
async def test_cancelled_unbind_finishes_runtime_invalidation_after_repository_mutation(tmp_path, monkeypatch):
    product, personality, repository, _, settings, user, pet = context(tmp_path)
    await product._sync_personality(pet)
    entered, release, cleared = threading.Event(), threading.Event(), asyncio.Event()
    original_unbind, original_clear = repository.unbind_pet, personality.clear_runtime
    def delayed(*args):
        original_unbind(*args)
        entered.set()
        assert release.wait(2)
    async def observe_clear():
        await original_clear()
        cleared.set()
    monkeypatch.setattr(repository, "unbind_pet", delayed)
    monkeypatch.setattr(personality, "clear_runtime", observe_clear)
    task = asyncio.create_task(product.unbind(user["user_id"], pet["pet_id"]))
    assert await asyncio.to_thread(entered.wait, 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    release.set()
    await asyncio.wait_for(cleared.wait(), 2)
    assert not settings.personality_runtime_path.exists()


@pytest.mark.asyncio
async def test_reset_does_not_wait_for_sync_finalizer_while_holding_lifecycle_lock(tmp_path, monkeypatch):
    product, _, repository, _, settings, user, pet = context(tmp_path)
    await product._sync_personality(pet)
    release = asyncio.Event()
    async def finalizer():
        await release.wait()
        await product._sync_personality(pet)
    task = asyncio.create_task(finalizer())
    product._finalizers.add(task)
    async def stop():
        release.set()
        return {"stopped": False, "state": "idle"}
    monkeypatch.setattr(product._motion, "stop", stop)
    result = await asyncio.wait_for(product.reset_product_data(user_id=user["user_id"]), 2)
    assert result["reset"] is True and not settings.personality_runtime_path.exists()
    with repository._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM pets").fetchone()[0] == 0
