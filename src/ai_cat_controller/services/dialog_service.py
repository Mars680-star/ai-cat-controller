"""Serialized dialog state transitions."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ai_cat_controller.adapters.base import AiCatAdapter, Capability
from ai_cat_controller.core.errors import AdapterNotImplementedError

LOGGER = logging.getLogger(__name__)


class DialogService:
    def __init__(self, adapter: AiCatAdapter) -> None:
        self._adapter = adapter
        self._lock = asyncio.Lock()
        self._state = "idle"

    @property
    def state(self) -> str:
        return self._state

    async def wake(self, request_id: str | None = None) -> dict[str, Any]:
        if not self._adapter.supports(Capability.WAKE_DIALOG):
            raise AdapterNotImplementedError("当前适配器不支持对话唤醒")
        async with self._lock:
            if self._state == "awake":
                return {
                    "dialog_state": self._state,
                    "request_id": request_id,
                    "changed": False,
                }
            await self._adapter.wake_dialog()
            self._state = "awake"
            LOGGER.info("dialog awakened")
            return {
                "dialog_state": self._state,
                "request_id": request_id,
                "changed": True,
            }

    async def interrupt(self, request_id: str | None = None) -> dict[str, Any]:
        if not self._adapter.supports(Capability.INTERRUPT_DIALOG):
            raise AdapterNotImplementedError("当前适配器不支持对话打断")
        async with self._lock:
            await self._adapter.interrupt_dialog()
            self._state = "interrupted"
            LOGGER.info("dialog interrupted")
            return {
                "dialog_state": self._state,
                "request_id": request_id,
                "changed": True,
            }

    async def shutdown(self) -> None:
        if self._state == "awake" and self._adapter.supports(Capability.INTERRUPT_DIALOG):
            try:
                await self.interrupt()
            except Exception:
                LOGGER.exception("dialog shutdown failed")
