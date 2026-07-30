"""Serialized, cancellable motion scheduling."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from ai_cat_controller.adapters.base import AiCatAdapter, Capability
from ai_cat_controller.core.errors import (
    ActionConflictError,
    ActionTimeoutError,
    AdapterNotImplementedError,
)

LOGGER = logging.getLogger(__name__)


class MotionService:
    def __init__(
        self,
        adapter: AiCatAdapter,
        *,
        command_timeout_seconds: float,
        cooldown_seconds: float,
    ) -> None:
        self._adapter = adapter
        self._command_timeout_seconds = command_timeout_seconds
        self._cooldown_seconds = cooldown_seconds
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._current_action = "idle"
        self._current_request_id: str | None = None
        self._generation = 0
        self._cooldown_until = 0.0
        self._stopping = False
        self._last_error: str | None = None

    @property
    def current_action(self) -> str:
        return self._current_action

    @property
    def last_error(self) -> str | None:
        return self._last_error

    async def _execute(
        self,
        generation: int,
        action_name: str,
        operation: Callable[[float, int], Awaitable[None]],
        intensity: float,
        duration_ms: int,
    ) -> None:
        timeout = max(
            self._command_timeout_seconds,
            duration_ms / 1000.0 + 0.5,
        )
        try:
            LOGGER.info("motion started: %s", action_name)
            await asyncio.wait_for(operation(intensity, duration_ms), timeout=timeout)
            LOGGER.info("motion completed: %s", action_name)
        except asyncio.CancelledError:
            LOGGER.info("motion cancelled: %s", action_name)
            raise
        except TimeoutError:
            self._last_error = f"{action_name} timed out"
            LOGGER.error("motion timed out: %s", action_name)
            try:
                await asyncio.wait_for(
                    self._adapter.stop_motion(),
                    timeout=self._command_timeout_seconds,
                )
            except Exception:
                LOGGER.exception("adapter stop failed after motion timeout")
        except Exception as exc:
            self._last_error = str(exc)
            LOGGER.exception("motion failed: %s", action_name)
        finally:
            async with self._lock:
                if generation == self._generation:
                    self._task = None
                    self._current_action = "idle"
                    self._current_request_id = None
                    self._cooldown_until = time.monotonic() + self._cooldown_seconds

    async def _start(
        self,
        *,
        capability: Capability,
        action_name: str,
        operation: Callable[[float, int], Awaitable[None]],
        intensity: float,
        duration_ms: int,
        request_id: str | None,
    ) -> dict[str, Any]:
        if not self._adapter.supports(capability):
            raise AdapterNotImplementedError(f"当前适配器不支持动作: {action_name}")

        async with self._lock:
            if self._stopping or (self._task is not None and not self._task.done()):
                raise ActionConflictError(f"已有动作正在执行: {self._current_action}")
            if time.monotonic() < self._cooldown_until:
                raise ActionConflictError("动作冷却中，请稍后重试")

            self._generation += 1
            generation = self._generation
            self._current_action = action_name
            self._current_request_id = request_id
            self._last_error = None
            self._task = asyncio.create_task(
                self._execute(
                    generation,
                    action_name,
                    operation,
                    intensity,
                    duration_ms,
                ),
                name=f"ai-cat-{action_name}-{generation}",
            )

        return {
            "action": action_name,
            "request_id": request_id,
            "state": "running",
        }

    async def shake_head(
        self, intensity: float, duration_ms: int, request_id: str | None
    ) -> dict[str, Any]:
        return await self._start(
            capability=Capability.SHAKE_HEAD,
            action_name="head_shake",
            operation=self._adapter.shake_head,
            intensity=intensity,
            duration_ms=duration_ms,
            request_id=request_id,
        )

    async def nod_head(
        self, intensity: float, duration_ms: int, request_id: str | None
    ) -> dict[str, Any]:
        return await self._start(
            capability=Capability.NOD_HEAD,
            action_name="head_nod",
            operation=self._adapter.nod_head,
            intensity=intensity,
            duration_ms=duration_ms,
            request_id=request_id,
        )

    async def wag_tail(
        self, intensity: float, duration_ms: int, request_id: str | None
    ) -> dict[str, Any]:
        return await self._start(
            capability=Capability.WAG_TAIL,
            action_name="tail_wag",
            operation=self._adapter.wag_tail,
            intensity=intensity,
            duration_ms=duration_ms,
            request_id=request_id,
        )

    async def stop(self) -> dict[str, Any]:
        if not self._adapter.supports(Capability.STOP_MOTION):
            raise AdapterNotImplementedError("当前适配器不支持停止动作")

        async with self._lock:
            if self._stopping:
                raise ActionConflictError("停止动作正在执行")
            task = self._task
            if task is None or task.done():
                return {"stopped": False, "state": "idle"}
            self._stopping = True
            self._generation += 1
            self._task = None
            self._current_action = "stopping"

        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        try:
            await asyncio.wait_for(
                self._adapter.stop_motion(),
                timeout=self._command_timeout_seconds,
            )
        except TimeoutError as exc:
            raise ActionTimeoutError("停止动作超时") from exc
        finally:
            async with self._lock:
                self._stopping = False
                self._current_action = "idle"
                self._current_request_id = None
                self._cooldown_until = time.monotonic() + self._cooldown_seconds
        return {"stopped": True, "state": "idle"}

    async def shutdown(self) -> None:
        async with self._lock:
            has_active_task = self._task is not None and not self._task.done()
        if has_active_task and self._adapter.supports(Capability.STOP_MOTION):
            try:
                await self.stop()
            except Exception:
                LOGGER.exception("motion shutdown failed")

    async def wait_until_idle(self, timeout: float = 5.0) -> None:
        async with self._lock:
            task = self._task
        if task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
            except TimeoutError as exc:
                raise ActionTimeoutError("等待动作结束超时") from exc
