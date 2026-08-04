"""Conservative SpaceMIT K1 adapter with status and dialog signal control."""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from ai_cat_controller.adapters.base import AiCatAdapter, Capability
from ai_cat_controller.adapters.command_runner import CommandResult, CommandRunner
from ai_cat_controller.core.config import Settings
from ai_cat_controller.core.errors import (
    ActionConflictError,
    AdapterNotImplementedError,
    DeviceUnavailableError,
)


class LocalK1Adapter(AiCatAdapter):
    mode = "local_k1"
    capabilities = frozenset(
        {
            Capability.BATTERY_STATUS,
            Capability.SHAKE_HEAD,
            Capability.NOD_HEAD,
            Capability.STOP_MOTION,
            Capability.WAKE_DIALOG,
            Capability.INTERRUPT_DIALOG,
            Capability.TEXT_DIALOG,
            Capability.SPEAK_TEXT,
        }
    )

    def __init__(self, settings: Settings, runner: CommandRunner) -> None:
        self._settings = settings
        self._runner = runner
        if settings.enable_tail_motion:
            self.capabilities = self.capabilities | {Capability.WAG_TAIL}
        self._connected = False
        self._connected_at = time.monotonic()

    async def connect(self) -> None:
        self._connected = True
        self._connected_at = time.monotonic()

    async def close(self) -> None:
        self._connected = False

    async def get_device_status(self) -> dict[str, Any]:
        dialog_status, power_status = await asyncio.gather(
            self.get_dialog_status(),
            asyncio.to_thread(self._read_power_status),
        )
        return {
            "connected": self._connected,
            "current_action": "unavailable",
            "last_action": None,
            "dialog_state": dialog_status["state"],
            "head_state": "idle",
            "tail_state": "idle" if self._settings.enable_tail_motion else "disabled",
            "action_count": 0,
            "last_action_at": None,
            "adapter_uptime_seconds": max(0.0, time.monotonic() - self._connected_at),
            **power_status,
        }

    @staticmethod
    def _read_sysfs_value(directory: Path, name: str) -> str:
        value = (directory / name).read_text(encoding="ascii").strip()
        if not value or len(value) > 128:
            raise ValueError(f"invalid {name} value")
        return value

    @classmethod
    def _read_bounded_int(
        cls,
        directory: Path,
        name: str,
        *,
        minimum: int,
        maximum: int,
    ) -> int:
        value = int(cls._read_sysfs_value(directory, name))
        if not minimum <= value <= maximum:
            raise ValueError(f"{name} is outside the supported range")
        return value

    @staticmethod
    def _normalized_battery_status(value: str) -> str:
        normalized = value.strip().lower().replace(" ", "_")
        if normalized in {
            "charging",
            "discharging",
            "full",
            "not_charging",
            "unknown",
        }:
            return normalized
        return "unknown"

    def _read_power_status(self) -> dict[str, Any]:
        battery = self._settings.battery_supply_path
        charger = self._settings.charger_supply_path
        errors: list[str] = []

        def read_int(
            directory: Path,
            name: str,
            minimum: int,
            maximum: int,
        ) -> int | None:
            try:
                return self._read_bounded_int(
                    directory,
                    name,
                    minimum=minimum,
                    maximum=maximum,
                )
            except (OSError, UnicodeError, ValueError):
                errors.append(name)
                return None

        battery_percent = read_int(battery, "capacity", 0, 100)
        battery_present_raw = read_int(battery, "present", 0, 1)
        voltage_uv = read_int(battery, "voltage_now", 0, 20_000_000)
        charger_online_raw = read_int(charger, "online", 0, 1)
        try:
            reported_battery_status = self._normalized_battery_status(
                self._read_sysfs_value(battery, "status")
            )
        except (OSError, UnicodeError, ValueError):
            errors.append("status")
            reported_battery_status = "unavailable"

        battery_present = (
            None if battery_present_raw is None else bool(battery_present_raw)
        )
        charger_online = (
            None if charger_online_raw is None else bool(charger_online_raw)
        )

        # The charger GPIO reacts to cable changes faster than the fuel gauge
        # status. Reconcile both readings so one API sample cannot report
        # "charging" while also reporting that the charger is disconnected.
        battery_status = reported_battery_status
        if battery_present is True and charger_online is False:
            battery_status = "discharging"
        elif battery_present is True and charger_online is True:
            if battery_percent == 100 or reported_battery_status == "full":
                battery_status = "full"
            elif reported_battery_status == "charging":
                battery_status = "charging"
            else:
                battery_status = "not_charging"

        if battery_present is False or battery_status in {"unknown", "unavailable"}:
            charging = None
        else:
            charging = battery_status == "charging"
        battery_available = battery_percent is not None and battery_present is True
        return {
            "battery_available": battery_available,
            "battery_percent": battery_percent,
            "battery_status": battery_status,
            "battery_present": battery_present,
            "battery_voltage_mv": (
                None if voltage_uv is None else round(voltage_uv / 1000)
            ),
            "charging": charging,
            "charger_online": charger_online,
            "battery_error": (
                None
                if not errors
                else "无法读取电源属性: " + ", ".join(sorted(set(errors)))
            ),
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
            "personality_id": None,
            "personality_revision": None,
            "voice_type": None,
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
            "personality_id": (
                str(payload["personality_id"])
                if payload.get("personality_id")
                else None
            ),
            "personality_revision": (
                str(payload["personality_revision"])
                if payload.get("personality_revision")
                else None
            ),
            "voice_type": (
                str(payload["voice_type"])
                if payload.get("voice_type")
                else None
            ),
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

    def capability_unavailable_reason(self, capability: Capability) -> str:
        if capability == Capability.WAG_TAIL:
            return (
                "当前 K1 样机尾部动作默认禁用；硬件修复并完成低速验收后，"
                "设置 AI_CAT_ENABLE_TAIL_MOTION=true 才可开放"
            )
        return super().capability_unavailable_reason(capability)

    async def _run_head_motor(
        self, actuator: str, intensity: float, duration_ms: int
    ) -> None:
        if not 0.1 <= intensity <= 1.0 or not 100 <= duration_ms <= 3000:
            raise ValueError("头部动作参数超出安全预设范围")
        speed = "1" if actuator == "head_lr" else "2"
        result = await self._runner.run(
            str(self._settings.hardware_binary),
            ["motor", actuator, speed],
        )
        if result.timed_out:
            raise DeviceUnavailableError("头部动作执行超时，已请求电机停止")
        if result.returncode == 0:
            return
        combined_output = f"{result.stdout}\n{result.stderr}".lower()
        if "busy" in combined_output or "正在执行" in combined_output:
            raise ActionConflictError("头部电机正在执行其他动作")
        raise DeviceUnavailableError(
            "头部动作执行失败",
            details={
                "returncode": result.returncode,
                "stderr": result.stderr,
            },
        )

    async def _run_tail_motor(self, intensity: float, duration_ms: int) -> None:
        if not self._settings.enable_tail_motion:
            raise AdapterNotImplementedError(
                self.capability_unavailable_reason(Capability.WAG_TAIL)
            )
        if not 0.1 <= intensity <= 1.0 or not 100 <= duration_ms <= 3000:
            raise ValueError("尾部动作参数超出安全预设范围")
        result = await self._runner.run(
            str(self._settings.hardware_binary),
            ["motor", "tail_lr", "1"],
        )
        if result.timed_out:
            raise DeviceUnavailableError("尾部动作执行超时，已请求电机停止")
        if result.returncode == 0:
            return
        combined_output = f"{result.stdout}\n{result.stderr}".lower()
        if "busy" in combined_output or "正在执行" in combined_output:
            raise ActionConflictError("电机正在执行其他动作")
        raise DeviceUnavailableError(
            "尾部动作执行失败",
            details={
                "returncode": result.returncode,
                "stderr": result.stderr,
            },
        )

    async def shake_head(self, intensity: float, duration_ms: int) -> None:
        await self._run_head_motor("head_lr", intensity, duration_ms)

    async def nod_head(self, intensity: float, duration_ms: int) -> None:
        await self._run_head_motor("head_ud", intensity, duration_ms)

    async def wag_tail(self, intensity: float, duration_ms: int) -> None:
        await self._run_tail_motor(intensity, duration_ms)

    async def stop_motion(self) -> bool:
        result = await self._runner.run(
            str(self._settings.hardware_binary),
            ["motor", "stop"],
        )
        if result.timed_out:
            raise DeviceUnavailableError("停止头部动作超时")
        if result.returncode != 0:
            raise DeviceUnavailableError(
                "停止头部动作失败",
                details={
                    "returncode": result.returncode,
                    "stderr": result.stderr,
                },
            )
        return "stop requested" in result.stdout.lower()

    async def wake_dialog(self) -> None:
        await self._signal_dialog("SIGUSR1")

    async def interrupt_dialog(self) -> None:
        await self._signal_dialog("SIGUSR2")

    def _write_dialog_request(
        self,
        content: str,
        request_id: str,
        kind: str,
    ) -> None:
        path = self._settings.dialog_text_request_path
        temporary_path = path.with_name(path.name + ".tmp")
        if path.exists():
            raise ActionConflictError("已有文字或播报请求等待语音进程处理")
        if not content or len(content) > 500:
            raise ValueError("对话文本长度必须为 1 到 500 个字符")
        if not request_id or len(request_id) > 64:
            raise ValueError("对话 request_id 无效")
        if kind not in {"question", "speak"}:
            raise ValueError("对话请求类型无效")

        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "version": 1,
                "kind": kind,
                "request_id": request_id,
                "content": content,
                "created_at_ms": int(time.time() * 1000),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ) + "\n"
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(temporary_path, flags, 0o600)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, path)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise

    async def send_text_dialog(self, content: str, request_id: str) -> None:
        await asyncio.to_thread(
            self._write_dialog_request,
            content,
            request_id,
            "question",
        )
        try:
            await self._signal_dialog("SIGHUP")
        except Exception:
            self._settings.dialog_text_request_path.unlink(missing_ok=True)
            raise

    async def speak_text(self, content: str, request_id: str) -> None:
        await asyncio.to_thread(
            self._write_dialog_request,
            content,
            request_id,
            "speak",
        )
        try:
            await self._signal_dialog("SIGHUP")
        except Exception:
            self._settings.dialog_text_request_path.unlink(missing_ok=True)
            raise
