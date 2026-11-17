"""Hardware gates run before tasks or commands; all motors here are fakes."""

import pytest
from pydantic import ValidationError

from ai_cat_controller.adapters.base import Capability
from ai_cat_controller.adapters.command_runner import CommandResult
from ai_cat_controller.adapters.local_k1 import LocalK1Adapter
from ai_cat_controller.adapters.mock import MockAiCatAdapter
from ai_cat_controller.core.config import Settings
from ai_cat_controller.core.errors import AdapterNotImplementedError
from ai_cat_controller.services.motion_service import MotionService


class Runner:
    def __init__(self):
        self.calls = []
    async def run(self, executable, arguments):
        self.calls.append((executable, arguments))
        assert arguments == ["motor", "stop"], "No movement may reach this runner"
        return CommandResult(0, "no active motor process", "", False)


@pytest.mark.parametrize("field,environment", [("enable_motion", "AI_CAT_ENABLE_MOTION"),
    ("has_tail", "AI_CAT_HAS_TAIL"), ("enable_tail_motion", "AI_CAT_ENABLE_TAIL_MOTION")])
@pytest.mark.parametrize("token,expected", [("true", True), ("TRUE", True), ("1", True), ("yes", True),
    ("ON", True), ("false", False), ("FALSE", False), ("0", False), ("no", False), ("off", False)])
def test_environment_motion_boolean_tokens_are_explicit(field, environment, token, expected):
    assert getattr(Settings.from_env({environment: token}), field) is expected


@pytest.mark.parametrize("environment", ["AI_CAT_ENABLE_MOTION", "AI_CAT_HAS_TAIL", "AI_CAT_ENABLE_TAIL_MOTION"])
@pytest.mark.parametrize("invalid", ["", " ", " false", "false ", " true ", "maybe", "-1", "2", "\nfalse", "null"])
def test_invalid_motion_boolean_environment_fails_startup(environment, invalid):
    with pytest.raises(ValidationError):
        Settings.from_env({environment: invalid})


def test_motion_gate_defaults_preserve_imported_machine_behavior():
    settings = Settings.from_env({})
    assert settings.enable_motion is True and settings.has_tail is True
    assert settings.enable_tail_motion is False


@pytest.mark.asyncio
@pytest.mark.parametrize("has_tail", [True, False])
async def test_direct_adapter_pause_blocks_every_motion_entry_but_stop(has_tail):
    settings = Settings(hardware_driver="local_k1", api_key_enabled=True, api_key="test-key",
        enable_motion=False, has_tail=has_tail, enable_tail_motion=True, motion_profile="k1_vendor_smooth")
    runner = Runner()
    adapter = LocalK1Adapter(settings, runner)
    for capability in (Capability.SHAKE_HEAD, Capability.NOD_HEAD, Capability.WAG_TAIL):
        assert not adapter.supports(capability)
    assert adapter.supports(Capability.STOP_MOTION)
    for operation in (adapter.shake_head(0.5, 600), adapter.nod_head(0.5, 600), adapter.wag_tail(0.5, 600)):
        with pytest.raises(AdapterNotImplementedError):
            await operation
    for preset in (*adapter._HEAD_MOTION_PRESETS, *adapter._TAIL_MOTION_PRESETS):
        assert not adapter.supports_motion_preset(preset)
        with pytest.raises(AdapterNotImplementedError) as error:
            await adapter.run_motion_preset(preset, 600)
        expected = "没有尾部机构" if not has_tail and preset in adapter._TAIL_MOTION_PRESETS else "动作已暂停"
        assert expected in error.value.message
    assert not runner.calls
    assert await adapter.stop_motion() is False
    assert runner.calls == [("/usr/bin/ai-toy_app", ["motor", "stop"])]


@pytest.mark.asyncio
async def test_no_tail_cannot_be_enabled_or_debug_unlocked_at_direct_adapter():
    runner = Runner()
    settings = Settings(hardware_driver="local_k1", api_key_enabled=True, api_key="test-key",
        has_tail=False, enable_tail_motion=True, debug_unlock_all_actions=True, motion_profile="k1_vendor_smooth")
    adapter = LocalK1Adapter(settings, runner)
    assert adapter.supports(Capability.SHAKE_HEAD) and adapter.supports(Capability.NOD_HEAD)
    assert not adapter.supports(Capability.WAG_TAIL)
    assert adapter.supports_motion_preset("quiet_companion")
    with pytest.raises(AdapterNotImplementedError, match="没有尾部机构"):
        await adapter.wag_tail(0.5, 600)
    for preset in adapter._TAIL_MOTION_PRESETS:
        assert not adapter.supports_motion_preset(preset)
        with pytest.raises(AdapterNotImplementedError, match="没有尾部机构"):
            await adapter.run_motion_preset(preset, 600)
    assert runner.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("has_tail", [True, False])
async def test_mock_scheduler_pause_rejects_before_task_future_or_generation(has_tail):
    adapter = MockAiCatAdapter(("volc-pulseaudio.service", "volc-conv-ai.service", "volc-k1-wake-word.service"))
    service = MotionService(adapter, command_timeout_seconds=1, cooldown_seconds=0,
                            enable_motion=False, has_tail=has_tail)
    assert service.motion_enabled is False and service.has_tail is has_tail
    operations = [service.shake_head(0.5, 600, "shake"), service.nod_head(0.5, 600, "nod"),
        service.wag_tail(0.5, 600, "wag"),
        service.run_sequence(action_name="quiet_companion", preset_name="quiet_companion",
            steps=((Capability.NOD_HEAD, 0.25, 600),), request_id="sequence")]
    for operation in operations:
        with pytest.raises(AdapterNotImplementedError):
            await operation
    assert service._task is None and service._generation == 0
    assert service._completion_futures == {} and service.current_action == "idle"
    assert service.unavailable_reason((Capability.WAG_TAIL,)) == ("动作已暂停" if has_tail else "当前设备没有尾部机构")
    await service.stop()


@pytest.mark.asyncio
async def test_no_tail_sequence_gate_cannot_be_bypassed_with_head_only_declared_steps():
    adapter = MockAiCatAdapter(("volc-pulseaudio.service", "volc-conv-ai.service", "volc-k1-wake-word.service"))
    service = MotionService(adapter, command_timeout_seconds=1, cooldown_seconds=0, has_tail=False)
    for preset in ("proud_pose", "greeting_combo", "celebration_combo"):
        with pytest.raises(AdapterNotImplementedError, match="没有尾部机构"):
            await service.run_sequence(action_name=preset, preset_name=preset,
                steps=((Capability.NOD_HEAD, 0.25, 600),), request_id="tail-" + preset)
    assert service._task is None and service._generation == 0 and service._completion_futures == {}
