"""Device metadata and cached service status queries."""

from __future__ import annotations

import asyncio
import platform
import socket
import sys
import time
from typing import Any

from ai_cat_controller.adapters.base import AiCatAdapter
from ai_cat_controller.core.config import Settings
from ai_cat_controller.services.dialog_service import DialogService
from ai_cat_controller.services.motion_service import MotionService


class DeviceService:
    def __init__(
        self,
        adapter: AiCatAdapter,
        settings: Settings,
        motion: MotionService,
        dialog: DialogService,
    ) -> None:
        self._adapter = adapter
        self._settings = settings
        self._motion = motion
        self._dialog = dialog
        self._started_at = time.monotonic()
        self._cache_lock = asyncio.Lock()
        self._services_cache: list[dict[str, Any]] | None = None
        self._services_cache_until = 0.0

    async def get_device_status(self) -> dict[str, Any]:
        adapter_status = await self._adapter.get_device_status()
        adapter_status.update(
            {
                "adapter_mode": self._settings.hardware_driver,
                "hostname": socket.gethostname(),
                "platform": platform.platform(),
                "python_version": sys.version.split()[0],
                "current_action": self._motion.current_action,
                "dialog_state": self._dialog.state,
                "last_error": self._motion.last_error,
                "uptime_seconds": max(0.0, time.monotonic() - self._started_at),
            }
        )
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
