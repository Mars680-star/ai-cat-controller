from typing import Any

import pytest

from ai_cat_controller.adapters.command_runner import CommandResult
from ai_cat_controller.adapters.local_k1 import LocalK1Adapter
from ai_cat_controller.core.config import Settings
from ai_cat_controller.core.errors import AdapterNotImplementedError


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    async def run(self, executable: str, args: list[str]) -> CommandResult:
        self.calls.append((executable, tuple(args)))
        output = "active" if args[0] == "is-active" else "enabled"
        return CommandResult(0, output, "", False)


class PartiallyFailingRunner(FakeRunner):
    async def run(self, executable: str, args: list[str]) -> CommandResult:
        if args[1] == "volc-conv-ai.service":
            raise FileNotFoundError("simulated query failure")
        return await super().run(executable, args)


def local_settings() -> Settings:
    return Settings(
        hardware_driver="local_k1",
        api_key_enabled=True,
        api_key="test-only-key",
    )


@pytest.mark.asyncio
async def test_local_status_uses_only_confirmed_read_commands() -> None:
    runner = FakeRunner()
    adapter = LocalK1Adapter(local_settings(), runner)  # type: ignore[arg-type]
    await adapter.connect()

    statuses = await adapter.get_services_status()

    assert len(statuses) == 3
    assert all(item["active"] and item["enabled"] for item in statuses)
    assert {args[0] for _, args in runner.calls} == {"is-active", "is-enabled"}
    assert {args[1] for _, args in runner.calls} == {
        "volc-pulseaudio.service",
        "volc-conv-ai.service",
        "volc-k1-wake-word.service",
    }


@pytest.mark.asyncio
async def test_one_service_failure_does_not_fail_status_response() -> None:
    adapter = LocalK1Adapter(
        local_settings(),
        PartiallyFailingRunner(),  # type: ignore[arg-type]
    )

    statuses = await adapter.get_services_status()
    by_name = {item["service_name"]: item for item in statuses}

    assert len(statuses) == 3
    assert by_name["volc-conv-ai.service"]["active"] is False
    assert "simulated query failure" in by_name["volc-conv-ai.service"]["error"]
    assert by_name["volc-pulseaudio.service"]["active"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("shake_head", (0.5, 600)),
        ("nod_head", (0.5, 600)),
        ("wag_tail", (0.5, 600)),
        ("stop_motion", ()),
        ("wake_dialog", ()),
        ("interrupt_dialog", ()),
    ],
)
async def test_local_actions_are_not_implemented(
    method: str, args: tuple[Any, ...]
) -> None:
    adapter = LocalK1Adapter(local_settings(), FakeRunner())  # type: ignore[arg-type]

    with pytest.raises(AdapterNotImplementedError):
        await getattr(adapter, method)(*args)
