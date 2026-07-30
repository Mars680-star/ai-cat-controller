"""Adapter construction."""

from ai_cat_controller.adapters.base import AiCatAdapter
from ai_cat_controller.adapters.command_runner import CommandRunner
from ai_cat_controller.adapters.local_k1 import LocalK1Adapter
from ai_cat_controller.adapters.mock import MockAiCatAdapter
from ai_cat_controller.core.config import Settings


def create_adapter(settings: Settings) -> AiCatAdapter:
    if settings.hardware_driver == "mock":
        return MockAiCatAdapter(settings.service_names)

    runner = CommandRunner(
        allowed_executables=frozenset({"/usr/bin/systemctl", "/bin/systemctl"}),
        allowed_services=frozenset(settings.service_names),
        allowed_service_signals={
            settings.dialog_service: frozenset({"SIGUSR1", "SIGUSR2"})
        },
        timeout_seconds=settings.command_timeout_seconds,
    )
    return LocalK1Adapter(settings, runner)
