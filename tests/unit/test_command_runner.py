import asyncio

import pytest

from ai_cat_controller.adapters.command_runner import CommandRunner
from ai_cat_controller.adapters.factory import create_adapter
from ai_cat_controller.core.config import Settings
from ai_cat_controller.core.errors import CommandNotAllowedError


def make_runner() -> CommandRunner:
    return CommandRunner(
        allowed_executables=frozenset(
            {"/usr/bin/systemctl", "/usr/bin/ai-toy_app", "/usr/bin/pactl"}
        ),
        allowed_services=frozenset({"volc-conv-ai.service"}),
        timeout_seconds=1.0,
        allowed_service_signals={
            "volc-conv-ai.service": frozenset({"SIGHUP", "SIGUSR1", "SIGUSR2"})
        },
        allowed_commands={
            "/usr/bin/systemctl": frozenset(
                {("--no-block", "start", "volc-conv-ai.service")}
            ),
            "/usr/bin/ai-toy_app": frozenset(
                {
                    ("motor", "head_lr", "1"),
                    ("motor", "head_ud", "2"),
                    ("motor", "stop"),
                }
            ),
            "/usr/bin/pactl": frozenset(
                {
                    ("get-sink-volume", "@DEFAULT_SINK@"),
                    ("set-sink-volume", "@DEFAULT_SINK@", "35%"),
                }
            ),
        },
        environment={"PULSE_SERVER": "unix:/var/run/pulse/native"},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("executable", "args"),
    [
        ("/bin/sh", ["is-active", "volc-conv-ai.service"]),
        ("/usr/bin/systemctl", ["restart", "volc-conv-ai.service"]),
        ("/usr/bin/systemctl", ["is-active", "arbitrary.service"]),
        ("/usr/bin/systemctl", ["is-active"]),
        (
            "/usr/bin/systemctl",
            ["kill", "--signal=SIGTERM", "volc-conv-ai.service"],
        ),
        (
            "/usr/bin/systemctl",
            ["kill", "--signal=SIGUSR1", "other.service"],
        ),
        ("/usr/bin/ai-toy_app", ["motor", "tail_lr", "2"]),
        ("/usr/bin/ai-toy_app", ["motor", "head_lr", "2"]),
        ("/usr/bin/ai-toy_app", ["motor", "head_lr", "3"]),
        ("/usr/bin/ai-toy_app", ["motor", "preset", "arbitrary"]),
        ("/usr/bin/ai-toy_app", ["motor", "all", "2"]),
        ("/usr/bin/pactl", ["set-sink-volume", "@DEFAULT_SINK@", "101%"]),
        ("/usr/bin/pactl", ["set-sink-volume", "sink-0", "35%"]),
    ],
)
async def test_runner_rejects_non_allowlisted_commands(
    executable: str, args: list[str]
) -> None:
    with pytest.raises(CommandNotAllowedError):
        await make_runner().run(executable, args)


@pytest.mark.asyncio
async def test_runner_uses_exec_without_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeProcess:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"active\n", b""

        def kill(self) -> None:
            captured["killed"] = True

        def terminate(self) -> None:
            captured["terminated"] = True

    async def fake_create_subprocess_exec(
        executable: str, *args: str, **kwargs: object
    ) -> FakeProcess:
        captured["executable"] = executable
        captured["args"] = args
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = await make_runner().run(
        "/usr/bin/systemctl", ["is-active", "volc-conv-ai.service"]
    )

    assert result.stdout == "active"
    assert captured["executable"] == "/usr/bin/systemctl"
    assert captured["args"] == ("is-active", "volc-conv-ai.service")
    assert "shell" not in captured["kwargs"]
    assert captured["kwargs"]["env"]["PULSE_SERVER"] == (
        "unix:/var/run/pulse/native"
    )


@pytest.mark.asyncio
async def test_runner_allows_fixed_default_sink_volume_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeProcess:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b""

        def kill(self) -> None:
            pass

        def terminate(self) -> None:
            pass

    async def fake_create_subprocess_exec(
        executable: str, *args: str, **kwargs: object
    ) -> FakeProcess:
        captured["executable"] = executable
        captured["args"] = args
        captured["env"] = kwargs["env"]
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    await make_runner().run(
        "/usr/bin/pactl",
        ["set-sink-volume", "@DEFAULT_SINK@", "35%"],
    )

    assert captured["executable"] == "/usr/bin/pactl"
    assert captured["args"] == (
        "set-sink-volume",
        "@DEFAULT_SINK@",
        "35%",
    )
    assert captured["env"]["PULSE_SERVER"] == "unix:/var/run/pulse/native"


@pytest.mark.asyncio
async def test_runner_allows_only_confirmed_dialog_signals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeProcess:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b""

        def kill(self) -> None:
            pass

        def terminate(self) -> None:
            pass

    async def fake_create_subprocess_exec(
        executable: str, *args: str, **kwargs: object
    ) -> FakeProcess:
        captured["args"] = args
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    await make_runner().run(
        "/usr/bin/systemctl",
        ["kill", "--signal=SIGHUP", "volc-conv-ai.service"],
    )

    assert captured["args"] == (
        "kill",
        "--signal=SIGHUP",
        "volc-conv-ai.service",
    )


@pytest.mark.asyncio
async def test_runner_allows_only_fixed_dialog_service_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeProcess:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b""

        def kill(self) -> None:
            pass

        def terminate(self) -> None:
            pass

    async def fake_create_subprocess_exec(
        executable: str, *args: str, **kwargs: object
    ) -> FakeProcess:
        del kwargs
        captured["executable"] = executable
        captured["args"] = args
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    await make_runner().run(
        "/usr/bin/systemctl",
        ["--no-block", "start", "volc-conv-ai.service"],
    )

    assert captured == {
        "executable": "/usr/bin/systemctl",
        "args": ("--no-block", "start", "volc-conv-ai.service"),
    }


@pytest.mark.asyncio
async def test_runner_allows_only_fixed_head_motor_profiles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeProcess:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"motion completed\n", b""

        def kill(self) -> None:
            pass

        def terminate(self) -> None:
            pass

    async def fake_create_subprocess_exec(
        executable: str, *args: str, **kwargs: object
    ) -> FakeProcess:
        captured["executable"] = executable
        captured["args"] = args
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = await make_runner().run(
        "/usr/bin/ai-toy_app", ["motor", "head_lr", "1"]
    )

    assert result.returncode == 0
    assert captured == {
        "executable": "/usr/bin/ai-toy_app",
        "args": ("motor", "head_lr", "1"),
    }


@pytest.mark.asyncio
async def test_factory_allowlists_only_selected_vendor_smooth_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[str, ...]] = []

    class FakeProcess:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"motion completed\n", b""

        def kill(self) -> None:
            pass

        def terminate(self) -> None:
            pass

    async def fake_create_subprocess_exec(
        executable: str, *args: str, **kwargs: object
    ) -> FakeProcess:
        del executable, kwargs
        captured.append(args)
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    adapter = create_adapter(
        Settings(
            hardware_driver="local_k1",
            api_key_enabled=True,
            api_key="test-only-key",
            motion_profile="k1_vendor_smooth",
        )
    )

    await adapter.shake_head(0.5, 600)
    await adapter.nod_head(0.5, 600)
    await adapter.run_motion_preset("quiet_companion", 1300)

    assert captured == [
        ("motor", "head_lr", "3"),
        ("motor", "head_ud", "3"),
        ("motor", "preset", "quiet_companion"),
    ]
    with pytest.raises(CommandNotAllowedError):
        await adapter._runner.run(  # type: ignore[attr-defined]
            "/usr/bin/ai-toy_app", ["motor", "head_lr", "1"]
        )
    with pytest.raises(CommandNotAllowedError):
        await adapter._runner.run(  # type: ignore[attr-defined]
            "/usr/bin/ai-toy_app",
            ["motor", "preset", "greeting_combo"],
        )
    with pytest.raises(CommandNotAllowedError):
        await adapter._runner.run(  # type: ignore[attr-defined]
            "/usr/bin/ai-toy_app",
            ["motor", "preset", "proud_pose"],
        )
