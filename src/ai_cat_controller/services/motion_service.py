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
MOTION_INPUT_NOISE_SETTLE_SECONDS = 1.0


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
        self._input_noise_guard_until = 0.0
        self._stopping = False
        self._last_error: str | None = None
        self._completion_futures: dict[str, asyncio.Future[str]] = {}

    @property
    def current_action(self) -> str:
        return self._current_action

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def input_noise_guard_active(self) -> bool:
        """Whether managed motor activity can produce false touch input."""

        return self._stopping or (
            self._task is not None and not self._task.done()
        )

    @property
    def sensor_noise_guard_active(self) -> bool:
        """Whether raw sensor input can still contain managed-motion noise."""

        return (
            self.input_noise_guard_active
            or time.monotonic() < self._input_noise_guard_until
        )

    def supports(self, capability: Capability) -> bool:
        return self._adapter.supports(capability)

    def unavailable_reason(self, capabilities: tuple[Capability, ...]) -> str | None:
        unsupported = [
            capability
            for capability in capabilities
            if not self._adapter.supports(capability)
        ]
        if not unsupported:
            return None
        return "; ".join(
            dict.fromkeys(
                self._adapter.capability_unavailable_reason(capability)
                for capability in unsupported
            )
        )

    async def _execute(
        self,
        generation: int,
        action_name: str,
        operation: Callable[[], Awaitable[None]],
        duration_ms: int,
        execution_token: str | None,
    ) -> None:
        timeout = max(
            self._command_timeout_seconds,
            duration_ms / 1000.0 + 0.5,
        )
        outcome = "completed"
        try:
            LOGGER.info("motion started: %s", action_name)
            await asyncio.wait_for(operation(), timeout=timeout)
            LOGGER.info("motion completed: %s", action_name)
        except asyncio.CancelledError:
            outcome = "cancelled"
            LOGGER.info("motion cancelled: %s", action_name)
            raise
        except TimeoutError:
            outcome = "timed_out"
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
            outcome = "failed"
            self._last_error = str(exc)
            LOGGER.exception("motion failed: %s", action_name)
        finally:
            if execution_token is not None:
                completion = self._completion_futures.get(execution_token)
                if completion is not None and not completion.done():
                    completion.set_result(outcome)
            async with self._lock:
                if generation == self._generation:
                    self._task = None
                    self._current_action = "idle"
                    self._current_request_id = None
                    self._input_noise_guard_until = (
                        time.monotonic() + MOTION_INPUT_NOISE_SETTLE_SECONDS
                    )
                    self._cooldown_until = time.monotonic() + self._cooldown_seconds

    async def _start(
        self,
        *,
        capabilities: tuple[Capability, ...],
        action_name: str,
        operation: Callable[[], Awaitable[None]],
        duration_ms: int,
        request_id: str | None,
        track_completion: bool = False,
    ) -> dict[str, Any]:
        unsupported = [
            capability
            for capability in capabilities
            if not self._adapter.supports(capability)
        ]
        if unsupported:
            raise AdapterNotImplementedError(
                self.unavailable_reason(capabilities)
                or f"当前适配器不支持动作: {action_name}",
                details={"capabilities": [item.value for item in unsupported]},
            )

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
            execution_token = (
                f"motion-{generation}-{time.monotonic_ns()}"
                if track_completion
                else None
            )
            if execution_token is not None:
                self._completion_futures[execution_token] = (
                    asyncio.get_running_loop().create_future()
                )
            self._task = asyncio.create_task(
                self._execute(
                    generation,
                    action_name,
                    operation,
                    duration_ms,
                    execution_token,
                ),
                name=f"ai-cat-{action_name}-{generation}",
            )

        return {
            "action": action_name,
            "request_id": request_id,
            "state": "running",
            "execution_token": execution_token,
        }

    async def shake_head(
        self, intensity: float, duration_ms: int, request_id: str | None
    ) -> dict[str, Any]:
        return await self._start(
            capabilities=(Capability.SHAKE_HEAD,),
            action_name="head_shake",
            operation=lambda: self._adapter.shake_head(intensity, duration_ms),
            duration_ms=duration_ms,
            request_id=request_id,
        )

    async def nod_head(
        self, intensity: float, duration_ms: int, request_id: str | None
    ) -> dict[str, Any]:
        return await self._start(
            capabilities=(Capability.NOD_HEAD,),
            action_name="head_nod",
            operation=lambda: self._adapter.nod_head(intensity, duration_ms),
            duration_ms=duration_ms,
            request_id=request_id,
        )

    async def wag_tail(
        self, intensity: float, duration_ms: int, request_id: str | None
    ) -> dict[str, Any]:
        return await self._start(
            capabilities=(Capability.WAG_TAIL,),
            action_name="tail_wag",
            operation=lambda: self._adapter.wag_tail(intensity, duration_ms),
            duration_ms=duration_ms,
            request_id=request_id,
        )

    async def run_sequence(
        self,
        *,
        action_name: str,
        preset_name: str | None = None,
        steps: tuple[tuple[Capability, float, int], ...],
        request_id: str | None,
    ) -> dict[str, Any]:
        """Run an allowlisted sequence while keeping the scheduler serialized."""

        if not steps:
            raise ValueError("motion sequence must contain at least one step")

        async def generic_operation() -> None:
            operations = {
                Capability.SHAKE_HEAD: self._adapter.shake_head,
                Capability.NOD_HEAD: self._adapter.nod_head,
                Capability.WAG_TAIL: self._adapter.wag_tail,
            }
            for capability, intensity, duration_ms in steps:
                await operations[capability](intensity, duration_ms)

        duration_ms = sum(step[2] for step in steps)
        operation = generic_operation
        if preset_name and self._adapter.supports_motion_preset(preset_name):
            operation = lambda: self._adapter.run_motion_preset(
                preset_name, duration_ms
            )

        return await self._start(
            capabilities=tuple(step[0] for step in steps),
            action_name=action_name,
            operation=operation,
            duration_ms=duration_ms,
            request_id=request_id,
            track_completion=True,
        )

    async def await_execution(
        self,
        execution_token: str,
        *,
        timeout: float | None = None,
    ) -> str:
        completion = self._completion_futures.get(execution_token)
        if completion is None:
            raise ValueError("unknown motion execution token")
        try:
            if timeout is None:
                return await asyncio.shield(completion)
            return await asyncio.wait_for(asyncio.shield(completion), timeout=timeout)
        finally:
            if completion.done():
                self._completion_futures.pop(execution_token, None)

    async def stop(self) -> dict[str, Any]:
        if not self._adapter.supports(Capability.STOP_MOTION):
            raise AdapterNotImplementedError("当前适配器不支持停止动作")

        async with self._lock:
            if self._stopping:
                raise ActionConflictError("停止动作正在执行")
            task = self._task
            self._stopping = True
            task_was_active = task is not None and not task.done()
            if task_was_active:
                self._generation += 1
                self._task = None
            self._current_action = "stopping"

        if task_was_active and task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        adapter_stopped = False
        try:
            adapter_stopped = await asyncio.wait_for(
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
                self._input_noise_guard_until = (
                    time.monotonic() + MOTION_INPUT_NOISE_SETTLE_SECONDS
                )
                self._cooldown_until = time.monotonic() + self._cooldown_seconds
        return {
            "stopped": task_was_active or adapter_stopped,
            "state": "idle",
        }

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
