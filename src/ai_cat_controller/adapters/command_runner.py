"""Strict subprocess runner for fixed, allowlisted system commands."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from ai_cat_controller.core.errors import CommandNotAllowedError


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool


class CommandRunner:
    def __init__(
        self,
        *,
        allowed_executables: frozenset[str],
        allowed_services: frozenset[str],
        timeout_seconds: float,
        allowed_service_signals: dict[str, frozenset[str]] | None = None,
        allowed_commands: dict[str, frozenset[tuple[str, ...]]] | None = None,
        max_output_bytes: int = 16_384,
        terminate_grace_seconds: float = 2.0,
    ) -> None:
        self._allowed_executables = allowed_executables
        self._allowed_services = allowed_services
        self._allowed_service_signals = allowed_service_signals or {}
        self._allowed_commands = allowed_commands or {}
        self._timeout_seconds = timeout_seconds
        self._max_output_bytes = max_output_bytes
        self._terminate_grace_seconds = terminate_grace_seconds

    def _validate(self, executable: str, args: list[str]) -> None:
        if executable not in self._allowed_executables:
            raise CommandNotAllowedError(f"可执行文件不在白名单中: {executable}")
        if tuple(args) in self._allowed_commands.get(executable, frozenset()):
            return
        if (
            len(args) == 2
            and args[0] in {"is-active", "is-enabled"}
            and args[1] in self._allowed_services
        ):
            return
        if len(args) == 3 and args[0] == "kill" and args[1].startswith("--signal="):
            signal_name = args[1].removeprefix("--signal=")
            if signal_name in self._allowed_service_signals.get(
                args[2], frozenset()
            ):
                return
        raise CommandNotAllowedError("命令参数不在固定白名单中")

    def _decode(self, value: bytes) -> str:
        return value[: self._max_output_bytes].decode("utf-8", errors="replace").strip()

    async def _terminate_process(
        self, process: asyncio.subprocess.Process
    ) -> tuple[bytes, bytes]:
        try:
            process.terminate()
        except ProcessLookupError:
            return await process.communicate()
        try:
            return await asyncio.wait_for(
                process.communicate(), timeout=self._terminate_grace_seconds
            )
        except TimeoutError:
            process.kill()
            return await process.communicate()

    async def run(self, executable: str, args: list[str]) -> CommandResult:
        self._validate(executable, args)
        process = await asyncio.create_subprocess_exec(
            executable,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self._timeout_seconds
            )
        except TimeoutError:
            stdout, stderr = await self._terminate_process(process)
            return CommandResult(
                returncode=process.returncode if process.returncode is not None else -1,
                stdout=self._decode(stdout),
                stderr=self._decode(stderr),
                timed_out=True,
            )
        except asyncio.CancelledError:
            await self._terminate_process(process)
            raise

        return CommandResult(
            returncode=process.returncode or 0,
            stdout=self._decode(stdout),
            stderr=self._decode(stderr),
            timed_out=False,
        )
