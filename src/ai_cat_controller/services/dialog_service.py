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
        self._wake_task: asyncio.Task[None] | None = None
        self._generation = 0

    @property
    def state(self) -> str:
        return self._state

    async def wake(self, request_id: str | None = None) -> dict[str, Any]:
        if not self._adapter.supports(Capability.WAKE_DIALOG):
            raise AdapterNotImplementedError("当前适配器不支持对话唤醒")
        async with self._lock:
            if self._wake_task is not None and not self._wake_task.done():
                return {
                    "dialog_state": "waking",
                    "request_id": request_id,
                    "changed": False,
                }
            self._generation += 1
            generation = self._generation
            self._state = "waking"
            task = asyncio.create_task(
                self._adapter.wake_dialog(),
                name=f"ai-cat-dialog-wake-{generation}",
            )
            self._wake_task = task

        try:
            await task
        except asyncio.CancelledError:
            async with self._lock:
                if generation == self._generation:
                    self._wake_task = None
                    self._state = "idle"
            LOGGER.info("dialog wake cancelled")
            raise
        except Exception:
            async with self._lock:
                if generation == self._generation:
                    self._wake_task = None
                    self._state = "error"
            LOGGER.exception("dialog wake failed")
            raise

        async with self._lock:
            if generation != self._generation:
                return {
                    "dialog_state": self._state,
                    "request_id": request_id,
                    "changed": False,
                }
            self._wake_task = None
            self._state = "awake"
            LOGGER.info("dialog awakened")
            return {
                "dialog_state": self._state,
                "request_id": request_id,
                "changed": True,
            }

    async def get_status(self) -> dict[str, Any]:
        status = await self._adapter.get_dialog_status()
        async with self._lock:
            if self._state in {"waking", "interrupting"}:
                status = {
                    **status,
                    "state": self._state,
                    "message": (
                        "正在请求开始聆听"
                        if self._state == "waking"
                        else "正在请求打断回答"
                    ),
                }
        return status

    async def interrupt(self, request_id: str | None = None) -> dict[str, Any]:
        if not self._adapter.supports(Capability.INTERRUPT_DIALOG):
            raise AdapterNotImplementedError("当前适配器不支持对话打断")
        async with self._lock:
            self._generation += 1
            wake_task = self._wake_task
            self._wake_task = None
            self._state = "interrupting"

        if wake_task is not None and not wake_task.done():
            wake_task.cancel()
            await asyncio.gather(wake_task, return_exceptions=True)

        try:
            await self._adapter.interrupt_dialog()
        except Exception:
            async with self._lock:
                self._state = "error"
            LOGGER.exception("dialog interrupt failed")
            raise
        async with self._lock:
            self._state = "interrupted"
            LOGGER.info("dialog interrupted")
            return {
                "dialog_state": self._state,
                "request_id": request_id,
                "changed": True,
            }

    async def shutdown(self) -> None:
        if (
            self._state in {"awake", "waking"}
            and self._adapter.supports(Capability.INTERRUPT_DIALOG)
        ):
            try:
                await self.interrupt()
            except Exception:
                LOGGER.exception("dialog shutdown failed")
