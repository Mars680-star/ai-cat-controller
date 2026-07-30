"""Strict subprocess runner for read-only, allowlisted system commands."""

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
        max_output_bytes: int = 16_384,
    ) -> None:
        self._allowed_executables = allowed_executables
        self._allowed_services = allowed_services
        self._timeout_seconds = timeout_seconds
        self._max_output_bytes = max_output_bytes

    def _validate(self, executable: str, args: list[str]) -> None:
        if executable not in self._allowed_executables:
            raise CommandNotAllowedError(f"可执行文件不在白名单中: {executable}")
        if len(args) != 2 or args[0] not in {"is-active", "is-enabled"}:
            raise CommandNotAllowedError("systemctl 只允许 is-active 和 is-enabled")
        if args[1] not in self._allowed_services:
            raise CommandNotAllowedError(f"服务名称不在白名单中: {args[1]}")

    def _decode(self, value: bytes) -> str:
        return value[: self._max_output_bytes].decode("utf-8", errors="replace").strip()

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
            process.kill()
            stdout, stderr = await process.communicate()
            return CommandResult(
                returncode=process.returncode if process.returncode is not None else -1,
                stdout=self._decode(stdout),
                stderr=self._decode(stderr),
                timed_out=True,
            )
        except asyncio.CancelledError:
            process.kill()
            await process.communicate()
            raise

        return CommandResult(
            returncode=process.returncode or 0,
            stdout=self._decode(stdout),
            stderr=self._decode(stderr),
            timed_out=False,
        )
