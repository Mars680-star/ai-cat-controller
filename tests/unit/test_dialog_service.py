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
async def test_repeated_wake_can_start_another_listening_turn() -> None:
    adapter = MockAiCatAdapter(SERVICES)
    await adapter.connect()
    service = DialogService(adapter)

    first = await service.wake()
    second = await service.wake()

    assert first["changed"] is True
    assert second["changed"] is True


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


@pytest.mark.asyncio
async def test_failed_wake_does_not_leave_service_waking() -> None:
    class FailingAdapter(MockAiCatAdapter):
        async def wake_dialog(self) -> None:
            raise RuntimeError("simulated wake failure")

    adapter = FailingAdapter(SERVICES)
    service = DialogService(adapter)

    with pytest.raises(RuntimeError, match="simulated wake failure"):
        await service.wake()

    assert service.state == "error"


@pytest.mark.asyncio
async def test_failed_interrupt_does_not_leave_service_interrupting() -> None:
    class FailingAdapter(MockAiCatAdapter):
        async def interrupt_dialog(self) -> None:
            raise RuntimeError("simulated interrupt failure")

    adapter = FailingAdapter(SERVICES)
    service = DialogService(adapter)

    with pytest.raises(RuntimeError, match="simulated interrupt failure"):
        await service.interrupt()

    assert service.state == "error"


@pytest.mark.asyncio
async def test_status_syncs_service_after_native_session_ends() -> None:
    adapter = MockAiCatAdapter(SERVICES)
    service = DialogService(adapter)
    await service.wake()
    await adapter.interrupt_dialog()

    status = await service.get_status()

    assert status["state"] == "interrupted"
    assert service.state == "interrupted"


@pytest.mark.asyncio
async def test_shutdown_does_not_interrupt_an_already_ended_native_session() -> None:
    class CountingAdapter(MockAiCatAdapter):
        interrupt_count = 0

        async def interrupt_dialog(self) -> None:
            self.interrupt_count += 1
            await super().interrupt_dialog()

    adapter = CountingAdapter(SERVICES)
    service = DialogService(adapter)
    await service.wake()
    await adapter.interrupt_dialog()

    await service.shutdown()

    assert adapter.interrupt_count == 1


@pytest.mark.asyncio
async def test_follow_up_config_survives_service_recreation(tmp_path) -> None:
    config_path = tmp_path / "dialog-runtime-config.json"
    adapter = MockAiCatAdapter(SERVICES)
    first_service = DialogService(adapter, config_path)

    saved = await first_service.update_config(65)
    second_service = DialogService(adapter, config_path)
    restored = await second_service.get_config()

    assert saved["follow_up_seconds"] == 65
    assert restored["follow_up_seconds"] == 65
    assert restored["source"] == "persisted"
    assert config_path.stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_offline_text_request_is_delegated_for_on_demand_start() -> None:
    class OfflineAdapter(MockAiCatAdapter):
        submitted: tuple[str, str] | None = None

        async def get_dialog_status(self) -> dict[str, object]:
            return {
                "state": "offline",
                "message": "waiting for wake word",
                "session_active": False,
                "stale": True,
            }

        async def send_text_dialog(self, content: str, request_id: str) -> None:
            self.submitted = (content, request_id)

    adapter = OfflineAdapter(SERVICES)
    service = DialogService(adapter)

    result = await service.send_text("网页按需提问", "web-offline-1")

    assert result["dialog_state"] == "queued"
    assert adapter.submitted == ("网页按需提问", "web-offline-1")
