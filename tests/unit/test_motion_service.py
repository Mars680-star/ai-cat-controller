import pytest

from ai_cat_controller.adapters.mock import MockAiCatAdapter
from ai_cat_controller.adapters.base import Capability
from ai_cat_controller.core.errors import ActionConflictError
from ai_cat_controller.services.motion_service import MotionService

SERVICES = (
    "volc-pulseaudio.service",
    "volc-conv-ai.service",
    "volc-k1-wake-word.service",
)


@pytest.mark.asyncio
async def test_motion_service_rejects_concurrency_and_stops() -> None:
    adapter = MockAiCatAdapter(SERVICES)
    await adapter.connect()
    service = MotionService(
        adapter,
        command_timeout_seconds=1.0,
        cooldown_seconds=0.0,
    )

    await service.shake_head(0.5, 1000, "first")
    with pytest.raises(ActionConflictError):
        await service.wag_tail(0.5, 100, "second")

    result = await service.stop()
    status = await adapter.get_device_status()

    assert result["stopped"] is True
    assert status["current_action"] == "idle"


@pytest.mark.asyncio
async def test_shutdown_restores_idle() -> None:
    adapter = MockAiCatAdapter(SERVICES)
    await adapter.connect()
    service = MotionService(
        adapter,
        command_timeout_seconds=1.0,
        cooldown_seconds=0.0,
    )
    await service.nod_head(0.5, 1000, None)

    await service.shutdown()

    assert (await adapter.get_device_status())["current_action"] == "idle"


@pytest.mark.asyncio
async def test_motion_finishes_without_blocking_caller() -> None:
    adapter = MockAiCatAdapter(SERVICES)
    await adapter.connect()
    service = MotionService(
        adapter,
        command_timeout_seconds=1.0,
        cooldown_seconds=0.0,
    )

    result = await service.wag_tail(0.5, 100, None)
    running = service.current_action
    await service.wait_until_idle()

    assert result["state"] == "running"
    assert running == "tail_wag"
    assert service.current_action == "idle"


@pytest.mark.asyncio
async def test_motion_sequence_reports_completion() -> None:
    adapter = MockAiCatAdapter(SERVICES)
    await adapter.connect()
    service = MotionService(
        adapter,
        command_timeout_seconds=1.0,
        cooldown_seconds=0.0,
    )

    result = await service.run_sequence(
        action_name="greeting_combo",
        steps=(
            (Capability.NOD_HEAD, 0.4, 10),
        ),
        request_id="sequence",
    )
    outcome = await service.await_execution(result["execution_token"])

    assert outcome == "completed"


@pytest.mark.asyncio
async def test_motion_sequence_prefers_named_adapter_preset() -> None:
    adapter = MockAiCatAdapter(SERVICES)
    await adapter.connect()
    service = MotionService(
        adapter,
        command_timeout_seconds=1.0,
        cooldown_seconds=0.0,
    )

    result = await service.run_sequence(
        action_name="quiet_companion",
        preset_name="quiet_companion",
        steps=((Capability.NOD_HEAD, 0.25, 10),),
        request_id="preset-sequence",
    )
    outcome = await service.await_execution(result["execution_token"])
    status = await adapter.get_device_status()

    assert outcome == "completed"
    assert status["last_action"] == "quiet_companion"
