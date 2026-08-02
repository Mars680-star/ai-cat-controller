"""In-memory adapter used for development and automated tests."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

from ai_cat_controller.adapters.base import AiCatAdapter, Capability


class MockAiCatAdapter(AiCatAdapter):
    mode = "mock"
    capabilities = frozenset(Capability)

    def __init__(self, service_names: tuple[str, ...]) -> None:
        self.connected = False
        self.current_action = "idle"
        self.last_action: str | None = None
        self.dialog_state = "idle"
        self.head_state = "idle"
        self.tail_state = "idle"
        self.action_count = 0
        self.last_action_at: str | None = None
        self.service_states = {
            name: {"active": True, "enabled": True, "error": None}
            for name in service_names
        }
        self._state_lock = asyncio.Lock()
        self._generation = 0
        self._connected_at = time.monotonic()

    async def connect(self) -> None:
        async with self._state_lock:
            self.connected = True
            self._connected_at = time.monotonic()

    async def close(self) -> None:
        await self.stop_motion()
        async with self._state_lock:
            self.connected = False

    async def get_device_status(self) -> dict[str, Any]:
        async with self._state_lock:
            return {
                "connected": self.connected,
                "current_action": self.current_action,
                "last_action": self.last_action,
                "dialog_state": self.dialog_state,
                "head_state": self.head_state,
                "tail_state": self.tail_state,
                "action_count": self.action_count,
                "last_action_at": self.last_action_at,
                "adapter_uptime_seconds": max(0.0, time.monotonic() - self._connected_at),
                "battery_available": True,
                "battery_percent": 86,
                "battery_status": "discharging",
                "battery_present": True,
                "battery_voltage_mv": 3900,
                "charging": False,
                "charger_online": False,
                "battery_error": None,
            }

    async def get_services_status(self) -> list[dict[str, Any]]:
        async with self._state_lock:
            return [
                {"service_name": name, **state}
                for name, state in self.service_states.items()
            ]

    async def get_dialog_status(self) -> dict[str, Any]:
        async with self._state_lock:
            state_map = {
                "idle": ("ready", "等待开始语音测试"),
                "awake": ("listening", "Mock 正在聆听"),
                "interrupted": ("interrupted", "Mock 回答已打断"),
            }
            state, message = state_map.get(
                self.dialog_state,
                (self.dialog_state, "Mock 对话状态"),
            )
            return {
                "state": state,
                "message": message,
                "session_active": self.dialog_state == "awake",
                "can_interrupt": self.dialog_state == "awake",
                "follow_up_deadline_ms": 0,
                "updated_at_ms": int(time.time() * 1000),
                "sequence": 0,
                "source": "mock",
                "stale": False,
            }

    async def _run_motion(
        self, action: str, part: str, intensity: float, duration_ms: int
    ) -> None:
        del intensity
        async with self._state_lock:
            self._generation += 1
            generation = self._generation
            self.current_action = action
            self.last_action = action
            self.last_action_at = datetime.now(timezone.utc).isoformat()
            self.action_count += 1
            if part == "head":
                self.head_state = action
            else:
                self.tail_state = action

        try:
            await asyncio.sleep(duration_ms / 1000.0)
        finally:
            async with self._state_lock:
                if generation == self._generation:
                    self.current_action = "idle"
                    if part == "head":
                        self.head_state = "idle"
                    else:
                        self.tail_state = "idle"

    async def shake_head(self, intensity: float, duration_ms: int) -> None:
        await self._run_motion("head_shake", "head", intensity, duration_ms)

    async def nod_head(self, intensity: float, duration_ms: int) -> None:
        await self._run_motion("head_nod", "head", intensity, duration_ms)

    async def wag_tail(self, intensity: float, duration_ms: int) -> None:
        await self._run_motion("tail_wag", "tail", intensity, duration_ms)

    async def stop_motion(self) -> bool:
        async with self._state_lock:
            stopped = self.current_action != "idle"
            self._generation += 1
            self.current_action = "idle"
            self.head_state = "idle"
            self.tail_state = "idle"
            return stopped

    async def wake_dialog(self) -> None:
        async with self._state_lock:
            self.dialog_state = "awake"

    async def interrupt_dialog(self) -> None:
        async with self._state_lock:
            self.dialog_state = "interrupted"
