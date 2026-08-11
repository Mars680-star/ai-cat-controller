"""Serialized cloud dialog lifecycle, text requests, and follow-up control.

Maintenance notes:
- Route every start, stop, interrupt, and text request through this service so
  concurrent browser, wake-word, and timeout operations stay ordered.
- K1 process signaling belongs in the adapter; product history belongs in the
  persistence-backed product service.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any

from ai_cat_controller.adapters.base import AiCatAdapter, Capability
from ai_cat_controller.core.errors import (
    ActionConflictError,
    AdapterNotImplementedError,
)

LOGGER = logging.getLogger(__name__)
DEFAULT_FOLLOW_UP_SECONDS = 30
MIN_FOLLOW_UP_SECONDS = 5
MAX_FOLLOW_UP_SECONDS = 120
MAX_CONFIG_BYTES = 4096
TEXT_DIALOG_READY_STATES = frozenset({"ready", "followup_listening", "interrupted"})
SPEAK_READY_STATES = frozenset({"ready", "interrupted"})
DIALOG_STARTABLE_STATES = frozenset(
    {"starting", "connecting", "recovering", "offline", "unavailable"}
)


class DialogService:
    def __init__(
        self,
        adapter: AiCatAdapter,
        config_path: Path = Path(".data/dialog-runtime-config.json"),
    ) -> None:
        self._adapter = adapter
        self._config_path = config_path
        self._lock = asyncio.Lock()
        self._config_lock = asyncio.Lock()
        self._state = "idle"
        self._wake_task: asyncio.Task[None] | None = None
        self._generation = 0

    @property
    def state(self) -> str:
        return self._state

    @staticmethod
    def _default_config() -> dict[str, Any]:
        return {
            "follow_up_seconds": DEFAULT_FOLLOW_UP_SECONDS,
            "minimum_seconds": MIN_FOLLOW_UP_SECONDS,
            "maximum_seconds": MAX_FOLLOW_UP_SECONDS,
            "source": "default",
        }

    def _read_config(self) -> dict[str, Any]:
        try:
            if self._config_path.stat().st_size > MAX_CONFIG_BYTES:
                raise ValueError("dialog config is too large")
            payload = json.loads(self._config_path.read_text(encoding="utf-8"))
            follow_up_seconds = payload.get("follow_up_seconds")
            if type(follow_up_seconds) is not int or not (
                MIN_FOLLOW_UP_SECONDS
                <= follow_up_seconds
                <= MAX_FOLLOW_UP_SECONDS
            ):
                raise ValueError("follow_up_seconds is outside the supported range")
        except FileNotFoundError:
            return self._default_config()
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            LOGGER.warning("invalid dialog runtime config %s: %s", self._config_path, exc)
            return self._default_config()
        return {
            "follow_up_seconds": follow_up_seconds,
            "minimum_seconds": MIN_FOLLOW_UP_SECONDS,
            "maximum_seconds": MAX_FOLLOW_UP_SECONDS,
            "source": "persisted",
        }

    def _write_config(self, follow_up_seconds: int) -> None:
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self._config_path.with_name(self._config_path.name + ".tmp")
        payload = json.dumps(
            {"version": 1, "follow_up_seconds": follow_up_seconds},
            ensure_ascii=False,
            separators=(",", ":"),
        ) + "\n"
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(temporary_path, flags, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as file:
                file.write(payload)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary_path, self._config_path)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise

    async def get_config(self) -> dict[str, Any]:
        async with self._config_lock:
            return await asyncio.to_thread(self._read_config)

    async def update_config(self, follow_up_seconds: int) -> dict[str, Any]:
        async with self._config_lock:
            await asyncio.to_thread(self._write_config, follow_up_seconds)
            return await asyncio.to_thread(self._read_config)

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
            elif status.get("session_active"):
                self._state = "awake"
            elif status.get("state") in {
                "ready",
                "interrupted",
                "offline",
                "unavailable",
            }:
                self._state = status["state"]
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

    async def send_text(
        self,
        content: str,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        if not self._adapter.supports(Capability.TEXT_DIALOG):
            raise AdapterNotImplementedError("当前适配器不支持文字提问")
        resolved_request_id = request_id or f"web-text-{uuid.uuid4().hex[:20]}"
        async with self._lock:
            status = await self._adapter.get_dialog_status()
            current_state = str(status.get("state", "unavailable"))
            if current_state not in TEXT_DIALOG_READY_STATES | DIALOG_STARTABLE_STATES:
                raise ActionConflictError(
                    "当前对话正在进行，请等待回答结束或先打断",
                    details={"dialog_state": current_state},
                )
            self._state = "submitting_text"
            try:
                await self._adapter.send_text_dialog(content, resolved_request_id)
            except Exception:
                self._state = "error"
                raise
            self._state = "thinking"
            return {
                "dialog_state": "queued",
                "request_id": resolved_request_id,
                "changed": True,
            }

    async def speak(
        self,
        content: str,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        if not self._adapter.supports(Capability.SPEAK_TEXT):
            raise AdapterNotImplementedError("当前适配器不支持主动播报")
        resolved_request_id = request_id or f"proactive-{uuid.uuid4().hex[:20]}"
        async with self._lock:
            status = await self._adapter.get_dialog_status()
            current_state = str(status.get("state", "unavailable"))
            if status.get("session_active") or current_state not in (
                SPEAK_READY_STATES | DIALOG_STARTABLE_STATES
            ):
                raise ActionConflictError(
                    "当前对话正在进行，本次主动播报已跳过",
                    details={"dialog_state": current_state},
                )
            self._state = "submitting_speech"
            try:
                await self._adapter.speak_text(content, resolved_request_id)
            except Exception:
                self._state = "error"
                raise
            self._state = "speaking"
            return {
                "dialog_state": "queued",
                "request_id": resolved_request_id,
                "changed": True,
            }

    async def shutdown(self) -> None:
        if not self._adapter.supports(Capability.INTERRUPT_DIALOG):
            return
        try:
            status = await self._adapter.get_dialog_status()
            if self._state == "waking" or status.get("session_active"):
                await self.interrupt()
        except Exception:
            LOGGER.exception("dialog shutdown failed")
