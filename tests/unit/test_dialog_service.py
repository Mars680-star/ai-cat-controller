import asyncio

import pytest

from ai_cat_controller.adapters.base import Capability
from ai_cat_controller.adapters.mock import MockAiCatAdapter
from ai_cat_controller.services.dialog_service import DialogService

SERVICES = (
    "volc-pulseaudio.service",
    "volc-conv-ai.service",
    "volc-k1-wake-word.service",
)


@pytest.mark.asyncio
async def test_repeated_wake_does_not_repeat_transition() -> None:
    adapter = MockAiCatAdapter(SERVICES)
    await adapter.connect()
    service = DialogService(adapter)

    first = await service.wake()
    second = await service.wake()

    assert first["changed"] is True
    assert second["changed"] is False


@pytest.mark.asyncio
async def test_shutdown_interrupts_awake_dialog() -> None:
    adapter = MockAiCatAdapter(SERVICES)
    await adapter.connect()
    service = DialogService(adapter)
    await service.wake()

    await service.shutdown()

    assert service.state == "interrupted"
    assert (await adapter.get_device_status())["dialog_state"] == "interrupted"


@pytest.mark.asyncio
async def test_interrupt_cancels_in_progress_wake() -> None:
    wake_started = asyncio.Event()
    wake_cancelled = asyncio.Event()

    class SlowWakeAdapter(MockAiCatAdapter):
        capabilities = frozenset(Capability)

        async def wake_dialog(self) -> None:
            wake_started.set()
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                wake_cancelled.set()
                raise

    adapter = SlowWakeAdapter(SERVICES)
    await adapter.connect()
    service = DialogService(adapter)
    wake_task = asyncio.create_task(service.wake())
    await wake_started.wait()

    result = await service.interrupt()
    await asyncio.gather(wake_task, return_exceptions=True)

    assert result["dialog_state"] == "interrupted"
    assert wake_cancelled.is_set()
