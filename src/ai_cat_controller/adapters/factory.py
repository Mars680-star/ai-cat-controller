"""Adapter construction."""

from ai_cat_controller.adapters.base import AiCatAdapter
from ai_cat_controller.adapters.command_runner import CommandRunner
from ai_cat_controller.adapters.local_k1 import LocalK1Adapter
from ai_cat_controller.adapters.mock import MockAiCatAdapter
from ai_cat_controller.core.config import Settings


def create_adapter(settings: Settings) -> AiCatAdapter:
    if settings.hardware_driver == "mock":
        return MockAiCatAdapter(settings.service_names)

    hardware_binary = str(settings.hardware_binary)
    hardware_commands = {
        ("motor", "head_lr", "1"),
        ("motor", "head_ud", "2"),
        ("motor", "stop"),
    }
    if settings.enable_tail_motion:
        hardware_commands.add(("motor", "tail_lr", "1"))

    runner = CommandRunner(
        allowed_executables=frozenset(
            {"/usr/bin/systemctl", "/bin/systemctl", hardware_binary}
        ),
        allowed_services=frozenset(settings.service_names),
        allowed_service_signals={
            settings.dialog_service: frozenset({"SIGUSR1", "SIGUSR2"})
        },
        allowed_commands={
            hardware_binary: frozenset(hardware_commands)
        },
        timeout_seconds=settings.command_timeout_seconds,
    )
    return LocalK1Adapter(settings, runner)
