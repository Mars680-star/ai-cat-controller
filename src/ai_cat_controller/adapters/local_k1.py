"""Conservative SpaceMIT K1 adapter with status and dialog signal control."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from ai_cat_controller.adapters.base import AiCatAdapter, Capability
from ai_cat_controller.adapters.command_runner import CommandResult, CommandRunner
from ai_cat_controller.core.config import Settings
from ai_cat_controller.core.errors import AdapterNotImplementedError, DeviceUnavailableError


class LocalK1Adapter(AiCatAdapter):
    mode = "local_k1"
    capabilities = frozenset(
        {
            Capability.WAKE_DIALOG,
            Capability.INTERRUPT_DIALOG,
        }
    )

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
        dialog_status = await self.get_dialog_status()
        return {
            "connected": self._connected,
            "current_action": "unavailable",
            "last_action": None,
            "dialog_state": dialog_status["state"],
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
    def _unavailable_dialog_status(message: str) -> dict[str, Any]:
        return {
            "state": "unavailable",
            "message": message,
            "session_active": False,
            "can_interrupt": False,
            "follow_up_deadline_ms": 0,
            "updated_at_ms": 0,
            "sequence": 0,
            "source": "local_k1",
            "stale": True,
        }

    @staticmethod
    def _safe_int(value: Any) -> int:
        if isinstance(value, bool):
            return 0
        if isinstance(value, (int, float)):
            return int(value)
        return 0

    def _read_dialog_status(self) -> dict[str, Any]:
        path = self._settings.dialog_status_path
        try:
            if path.stat().st_size > 16_384:
                return self._unavailable_dialog_status("对话状态文件超过安全大小")
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return self._unavailable_dialog_status("对话状态文件尚未生成")
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            return self._unavailable_dialog_status(f"无法读取对话状态: {exc}")

        state = payload.get("state")
        if not isinstance(state, str) or not state:
            return self._unavailable_dialog_status("对话状态文件缺少 state")
        updated_at_ms = self._safe_int(payload.get("updated_at_ms", 0))
        native_pid = self._safe_int(payload.get("pid", 0))
        age_ms = max(int(time.time() * 1000 - updated_at_ms), 0)
        native_process_alive = native_pid > 0 and Path(f"/proc/{native_pid}").exists()
        return {
            "state": state,
            "message": str(payload.get("message", "")),
            "session_active": bool(payload.get("session_active", False)),
            "can_interrupt": bool(payload.get("can_interrupt", False)),
            "follow_up_deadline_ms": self._safe_int(
                payload.get("follow_up_deadline_ms", 0)
            ),
            "updated_at_ms": updated_at_ms,
            "sequence": self._safe_int(payload.get("sequence", 0)),
            "source": "local_k1",
            "stale": age_ms > 30_000 and not native_process_alive,
        }

    async def get_dialog_status(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._read_dialog_status)

    async def _signal_dialog(self, signal_name: str) -> None:
        result = await self._runner.run(
            str(self._settings.systemctl_binary),
            [
                "kill",
                f"--signal={signal_name}",
                self._settings.dialog_service,
            ],
        )
        if result.timed_out:
            raise DeviceUnavailableError("发送对话控制信号超时")
        if result.returncode != 0:
            raise DeviceUnavailableError(
                "发送对话控制信号失败",
                details={
                    "returncode": result.returncode,
                    "stderr": result.stderr,
                },
            )

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
        await self._signal_dialog("SIGUSR1")

    async def interrupt_dialog(self) -> None:
        await self._signal_dialog("SIGUSR2")
