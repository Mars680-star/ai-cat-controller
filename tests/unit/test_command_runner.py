import asyncio

import pytest

from ai_cat_controller.adapters.command_runner import CommandRunner
from ai_cat_controller.core.errors import CommandNotAllowedError


def make_runner() -> CommandRunner:
    return CommandRunner(
        allowed_executables=frozenset({"/usr/bin/systemctl"}),
        allowed_services=frozenset({"volc-conv-ai.service"}),
        timeout_seconds=1.0,
        allowed_service_signals={
            "volc-conv-ai.service": frozenset({"SIGUSR1", "SIGUSR2"})
        },
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

    async def fake_create_subprocess_exec(
        executable: str, *args: str, **kwargs: object
    ) -> FakeProcess:
        captured["args"] = args
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    await make_runner().run(
        "/usr/bin/systemctl",
        ["kill", "--signal=SIGUSR2", "volc-conv-ai.service"],
    )

    assert captured["args"] == (
        "kill",
        "--signal=SIGUSR2",
        "volc-conv-ai.service",
    )
