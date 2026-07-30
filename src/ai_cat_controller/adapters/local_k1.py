"""Read-only first-stage adapter for a SpaceMIT K1 board."""

from __future__ import annotations

import time
from typing import Any

from ai_cat_controller.adapters.base import AiCatAdapter
from ai_cat_controller.adapters.command_runner import CommandResult, CommandRunner
from ai_cat_controller.core.config import Settings
from ai_cat_controller.core.errors import AdapterNotImplementedError


class LocalK1Adapter(AiCatAdapter):
    mode = "local_k1"

    def __init__(self, settings: Settings, runner: CommandRunner) -> None:
        self._settings = settings
        self._runner = runner
        self._connected = False
        self._connected_at = time.monotonic()

    async def connect(self) -> None:
        self._connected = True
        self._connected_at = time.monotonic()

    async def close(self) -> None:
        self._connected = False

    async def get_device_status(self) -> dict[str, Any]:
        return {
            "connected": self._connected,
            "current_action": "unavailable",
            "last_action": None,
            "dialog_state": "unavailable",
            "head_state": "unavailable",
            "tail_state": "unavailable",
            "action_count": 0,
            "last_action_at": None,
            "adapter_uptime_seconds": max(0.0, time.monotonic() - self._connected_at),
        }

    @staticmethod
    def _normal_status(result: CommandResult, known_values: set[str]) -> str | None:
        if result.timed_out:
            return "状态检查超时"
        if result.stdout in known_values:
            return None
        if result.stderr:
            return result.stderr
        return f"无法识别 systemctl 输出，returncode={result.returncode}"

    async def _query_service(self, verb: str, service_name: str) -> CommandResult:
        try:
            return await self._runner.run(
                str(self._settings.systemctl_binary),
                [verb, service_name],
            )
        except Exception as exc:
            return CommandResult(
                returncode=-1,
                stdout="",
                stderr=f"{type(exc).__name__}: {exc}",
                timed_out=False,
            )

    async def get_services_status(self) -> list[dict[str, Any]]:
        statuses: list[dict[str, Any]] = []
        for service_name in self._settings.service_names:
            active_result = await self._query_service("is-active", service_name)
            enabled_result = await self._query_service("is-enabled", service_name)
            errors = [
                error
                for error in (
                    self._normal_status(
                        active_result,
                        {"active", "inactive", "failed", "activating", "deactivating"},
                    ),
                    self._normal_status(
                        enabled_result,
                        {
                            "enabled",
                            "disabled",
                            "static",
                            "masked",
                            "indirect",
                            "generated",
                            "transient",
                        },
                    ),
                )
                if error
            ]
            statuses.append(
                {
                    "service_name": service_name,
                    "active": active_result.stdout == "active",
                    "enabled": enabled_result.stdout == "enabled",
                    "error": "; ".join(errors) if errors else None,
                }
            )
        return statuses

    @staticmethod
    def _not_implemented(interface_name: str) -> AdapterNotImplementedError:
        return AdapterNotImplementedError(
            f"Local K1 的 {interface_name} 真实接口尚未在第一阶段开放"
        )

    async def shake_head(self, intensity: float, duration_ms: int) -> None:
        raise self._not_implemented("摇头")

    async def nod_head(self, intensity: float, duration_ms: int) -> None:
        raise self._not_implemented("点头")

    async def wag_tail(self, intensity: float, duration_ms: int) -> None:
        raise self._not_implemented("摇尾")

    async def stop_motion(self) -> None:
        raise self._not_implemented("停止动作")

    async def wake_dialog(self) -> None:
        raise self._not_implemented("对话唤醒")

    async def interrupt_dialog(self) -> None:
        raise self._not_implemented("对话打断")
