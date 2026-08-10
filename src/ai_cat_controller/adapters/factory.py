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
    systemctl_binary = str(settings.systemctl_binary)
    pulseaudio_ctl_binary = str(settings.pulseaudio_ctl_binary)
    smooth_profile = settings.motion_profile == "k1_vendor_smooth"
    hardware_commands = {
        ("motor", "head_lr", "3" if smooth_profile else "1"),
        ("motor", "head_ud", "3" if smooth_profile else "2"),
        ("motor", "stop"),
    }
    if smooth_profile:
        hardware_commands.update(
            {
                ("motor", "preset", "quiet_companion"),
            }
        )
    if settings.enable_tail_motion:
        hardware_commands.add(
            ("motor", "tail_lr", "3" if smooth_profile else "1")
        )
        if smooth_profile:
            hardware_commands.update(
                {
                    ("motor", "preset", "proud_pose"),
                    ("motor", "preset", "greeting_combo"),
                    ("motor", "preset", "celebration_combo"),
                }
            )
    pulseaudio_commands = {
        ("get-sink-volume", "@DEFAULT_SINK@"),
        ("get-sink-mute", "@DEFAULT_SINK@"),
        ("set-sink-mute", "@DEFAULT_SINK@", "0"),
        ("set-sink-mute", "@DEFAULT_SINK@", "1"),
        *(
            ("set-sink-volume", "@DEFAULT_SINK@", f"{percent}%")
            for percent in range(101)
        ),
    }

    runner = CommandRunner(
        allowed_executables=frozenset(
            {
                "/usr/bin/systemctl",
                "/bin/systemctl",
                hardware_binary,
                pulseaudio_ctl_binary,
            }
        ),
        allowed_services=frozenset(settings.service_names),
        allowed_service_signals={
            settings.dialog_service: frozenset(
                {"SIGHUP", "SIGUSR1", "SIGUSR2"}
            )
        },
        allowed_commands={
            hardware_binary: frozenset(hardware_commands),
            systemctl_binary: frozenset(
                {("--no-block", "start", settings.dialog_service)}
            ),
            pulseaudio_ctl_binary: frozenset(pulseaudio_commands),
        },
        timeout_seconds=settings.command_timeout_seconds,
        environment={"PULSE_SERVER": settings.pulse_server},
    )
    return LocalK1Adapter(settings, runner)
