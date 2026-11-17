"""Device metadata and cached service status queries."""

from __future__ import annotations

import asyncio
import platform
import socket
import sys
import time
from pathlib import Path
from typing import Any

from ai_cat_controller.adapters.base import AiCatAdapter
from ai_cat_controller.core.config import Settings
from ai_cat_controller.core.operation_gate import DeviceOperationGate
from ai_cat_controller.services.dialog_service import DialogService
from ai_cat_controller.services.motion_service import MotionService


class DeviceService:
    def __init__(
        self,
        adapter: AiCatAdapter,
        settings: Settings,
        motion: MotionService,
        dialog: DialogService,
        *,
        maintenance_gate: DeviceOperationGate | None = None,
    ) -> None:
        self._adapter = adapter
        self._maintenance_gate = maintenance_gate
        self._settings = settings
        self._motion = motion
        self._dialog = dialog
        self._started_at = time.monotonic()
        self._cache_lock = asyncio.Lock()
        self._services_cache: list[dict[str, Any]] | None = None
        self._services_cache_until = 0.0

    @staticmethod
    def _read_device_serial(path: Path) -> str | None:
        try:
            serial = path.read_bytes().replace(b"\x00", b"").decode("ascii").strip()
        except (OSError, UnicodeError):
            return None
        if not serial or len(serial) > 64:
            return None
        return serial

    async def get_device_status(self) -> dict[str, Any]:
        adapter_status = await self._adapter.get_device_status()
        device_serial = None
        if self._settings.hardware_driver == "local_k1":
            device_serial = await asyncio.to_thread(
                self._read_device_serial,
                self._settings.device_serial_path,
            )
        dialog_state = adapter_status.get("dialog_state", self._dialog.state)
        if self._dialog.state in {"waking", "interrupting"}:
            dialog_state = self._dialog.state
        adapter_status.update(
            {
                "adapter_mode": self._settings.hardware_driver,
                "device_serial": device_serial,
                "hostname": socket.gethostname(),
                "platform": platform.platform(),
                "python_version": sys.version.split()[0],
                "current_action": self._motion.current_action,
                "dialog_state": dialog_state,
                "last_error": self._motion.last_error,
                "uptime_seconds": max(0.0, time.monotonic() - self._started_at),
                "motion_enabled": self._settings.enable_motion,
                "has_tail": self._settings.has_tail,
            }
        )
        if not self._settings.has_tail:
            adapter_status["tail_state"] = "not_present"
        elif not self._settings.enable_motion:
            adapter_status["tail_state"] = "disabled"
        if not self._settings.enable_motion:
            adapter_status["head_state"] = "disabled"
        return adapter_status

    async def get_services_status(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        async with self._cache_lock:
            if self._services_cache is not None and now < self._services_cache_until:
                return [dict(item) for item in self._services_cache]
            statuses = await self._adapter.get_services_status()
            self._services_cache = [dict(item) for item in statuses]
            self._services_cache_until = (
                now + self._settings.service_status_cache_seconds
            )
            return statuses
