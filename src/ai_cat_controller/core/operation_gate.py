"""Task-aware fence with optional cooperative Linux process coordination.

Ordinary work finishes before maintenance may erase data. New work never queues
behind maintenance, and ingress tickets prevent a slow old request from being
admitted after a generation change. Caller cancellation does not abandon a disk
thread or an already accepted write. Independent safety stop/interrupt methods
deliberately do not use ordinary admission.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from dataclasses import dataclass
from functools import wraps
import inspect
import math
from typing import Any

from .errors import ActionConflictError
from .process_gate import ProcessGateError, ProcessLease, ProcessOperationGate, ProcessTicket


@dataclass(eq=False, slots=True)
class _Lease:
    gate: Any
    epoch: int
    kind: str = "operation"
    active: bool = True
    started: bool = False
    owner: asyncio.Task | None = None
    process_lease: ProcessLease | None = None


@dataclass(frozen=True, slots=True)
class _Ticket:
    gate: Any
    epoch: int
    process_ticket: ProcessTicket | None = None


_leases: ContextVar[tuple[_Lease, ...]] = ContextVar("device_operation_leases", default=())
_tickets: ContextVar[tuple[_Ticket, ...]] = ContextVar("device_request_tickets", default=())


def current_process_work_lease(*, reject_unleased_context: bool = False) -> ProcessLease | None:
    """For fixed subprocess execution; never hand an exclusive lock to a child."""
    if reject_unleased_context:
        for lease in reversed(_leases.get()):
            if lease.gate._process_gate is not None:
                if lease.active and lease.kind == "operation" and lease.process_lease is not None:
                    return lease.process_lease
                raise ProcessGateError()
        if any(ticket.gate._process_gate is not None for ticket in _tickets.get()):
            raise ProcessGateError()
    return next((lease.process_lease for lease in reversed(_leases.get())
        if lease.active and lease.kind == "operation" and lease.process_lease is not None), None)


def _conflict() -> ActionConflictError:
    return ActionConflictError("设备正在维护或请求已失效，请刷新状态后重新确认；不会自动重试")


def device_operation(method):
    """Keep standalone service construction compatible; app wiring opts in."""
    @wraps(method)
    async def guarded(self, *args, **kwargs):
        gate = getattr(self, "_maintenance_gate", None)
        operation = method(self, *args, **kwargs)
        return await operation if gate is None else await gate.operation(operation)
    return guarded


class DeviceOperationGate:
    def __init__(self, *, timeout_seconds: float = 5.0, process_gate: ProcessOperationGate | None = None):
        if (type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
                or timeout_seconds <= 0 or timeout_seconds > 60):
            raise ValueError("A finite positive maintenance drain timeout is required")
        self._timeout = float(timeout_seconds)
        self._process_gate = process_gate
        self._generation = 0
        self._closing = False
        self._closed = False
        self._active: set[_Lease] = set()
        self._tasks: set[asyncio.Task] = set()
        self._changed = asyncio.Event()
        self._loop = None

    def process_context_gate(self, *, expected_path=None) -> DeviceOperationGate:
        """Reuse the original ingress/actual-work owner for this IPC domain.

        A directly constructed adapter must not take a fresh ticket around an
        old HTTP scope. Keep the existing task/required-child rules instead of
        transferring a lease between coordinator objects or exposing its FD.
        """
        if expected_path is not None and (self._process_gate is None or
                self._process_gate.path != expected_path):
            raise _conflict()
        if self._process_gate is None:
            return self
        try:
            for context in (*reversed(_leases.get()), *_tickets.get()):
                candidate = context.gate
                if (candidate._process_gate is not None and
                        self._process_gate.same_domain(candidate._process_gate)):
                    return candidate
            # Validate even when no request context existed.
            self._process_gate.same_domain(self._process_gate)
        except ProcessGateError as error:
            raise _conflict() from error
        return self

    @property
    def epoch(self) -> int:
        return self._generation

    @property
    def closing(self) -> bool:
        return self._closing

    @property
    def active_operations(self) -> int:
        return len(self._active)

    def _check_loop(self):
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        if self._loop is not loop:
            raise _conflict()
        return loop

    def _lease(self):
        return next((lease for lease in reversed(_leases.get()) if lease.gate is self), None)

    def _ticket(self):
        # Ingress identity is the earliest matching origin, not whichever
        # collaborator most recently wrapped the request in another scope.
        # A peer's local generation is not a replacement for that origin.
        try:
            for ticket in _tickets.get():
                if ticket.gate is self:
                    return ticket
                if (self._process_gate is not None and ticket.gate._process_gate is not None
                        and self._process_gate.same_domain(ticket.gate._process_gate)):
                    return ticket
        except ProcessGateError as error:
            raise _conflict() from error
        return None

    @staticmethod
    def _ticket_is_current(ticket):
        origin = ticket.gate
        return not origin._closed and not origin._closing and ticket.epoch == origin._generation

    @staticmethod
    def _close(operation):
        if inspect.iscoroutine(operation):
            operation.close()

    def _ordinary_admission(self) -> int:
        self._check_loop()
        lease = self._lease()
        if lease is not None and lease.active:
            # An admitted parent may reserve its required finalizer while the
            # drain is pending. Its descendants are included in the drain too.
            if lease.kind == "operation":
                return lease.epoch
            raise _conflict()  # no unawaited ordinary escape from maintenance
        ticket = self._ticket()
        if (self._closed or self._closing or
                lease is not None and (not lease.active or lease.epoch != self._generation) or
                ticket is not None and not self._ticket_is_current(ticket)):
            raise _conflict()
        return self._generation

    @contextmanager
    def request_scope(self):
        """Capture once before body/auth; nested scopes retain their origin."""
        try:
            ticket = self._ticket()
        except ActionConflictError:
            # Reads still enter an unsafe/unavailable protocol scope. This
            # deliberately-unwritable ticket must never become a fresh one.
            ticket = _Ticket(self, self._generation, None)
        if ticket is None:
            process_ticket = None
            if self._process_gate is not None:
                try:
                    process_ticket = self._process_gate.snapshot()
                except ProcessGateError:
                    pass  # Pure reads remain possible; this ingress cannot write.
            ticket = _Ticket(self, self._generation, process_ticket)
        token = _tickets.set((*_tickets.get(), ticket))
        try:
            yield
        finally:
            _tickets.reset(token)

    def checkpoint(self):
        """For optional new effects after a wait; never split an atomic write."""
        self._check_loop()
        lease, ticket = self._lease(), self._ticket()
        if lease is not None and lease.active and lease.kind == "maintenance" and lease.owner is asyncio.current_task():
            return
        if (self._closed or self._closing or
                lease is not None and (not lease.active or lease.epoch != self._generation) or
                ticket is not None and not self._ticket_is_current(ticket)):
            raise _conflict()
        if self._process_gate is not None:
            if ticket is not None and ticket.process_ticket is None:
                raise _conflict()
            expected = lease.process_lease if lease is not None and lease.active else (
                ticket.process_ticket if ticket is not None else None)
            try:
                self._process_gate.checkpoint(expected)
            except ProcessGateError as error:
                raise _conflict() from error

    def _release(self, lease):
        lease.active = False
        if lease.process_lease is not None:
            process_lease, lease.process_lease = lease.process_lease, None
            process_lease.close()
        self._active.discard(lease)
        self._changed.set()

    def _track(self, task, *, lease=None, operation=None):
        self._tasks.add(task)
        def finished(future):
            self._tasks.discard(future)
            if lease is not None:
                if not lease.started:
                    self._close(operation)
                self._release(lease)
            if not future.cancelled():
                future.exception()  # observe failures even after caller leaves
        task.add_done_callback(finished)
        return task

    @staticmethod
    async def _settle(task):
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if task.done() and not task.cancelled():
            task.exception()

    async def _body(self, operation, lease):
        lease.owner = asyncio.current_task()
        lease.started = True
        token = _leases.set((*_leases.get(), lease))
        try:
            return await operation
        finally:
            _leases.reset(token)

    async def _reserved(self, operation, lease, cancellable):
        task = asyncio.create_task(self._body(operation, lease))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if cancellable:
                task.cancel()
            await self._settle(task)
            raise
        finally:
            self._release(lease)

    def spawn(self, operation: Coroutine, *, name=None, cancellable=False) -> asyncio.Task:
        """Reserve synchronously; cancelling a motor task waits its cleanup."""
        try:
            epoch = self._ordinary_admission()
            process_lease = None
            if self._process_gate is not None:
                parent, ticket = self._lease(), self._ticket()
                if parent is not None and parent.active and parent.kind == "operation":
                    process_lease = self._process_gate.normal(inherit=parent.process_lease)
                else:
                    if ticket is not None and ticket.process_ticket is None:
                        raise _conflict()
                    process_lease = self._process_gate.normal(
                        expected=ticket.process_ticket if ticket is not None else None)
        except ProcessGateError as error:
            self._close(operation)
            raise _conflict() from error
        except BaseException:
            self._close(operation)
            raise
        lease = _Lease(self, epoch, process_lease=process_lease)
        self._active.add(lease)
        self._changed.set()
        task = asyncio.create_task(self._reserved(operation, lease, cancellable), name=name)
        return self._track(task, lease=lease, operation=operation)

    def detached_task(self, operation: Coroutine, *, name=None) -> asyncio.Task:
        """A sleeping monitor is not admitted work; each iteration must enter."""
        context = copy_context()
        context.run(_leases.set, tuple(lease for lease in _leases.get() if lease.gate is not self))
        context.run(_tickets.set, tuple(ticket for ticket in _tickets.get() if ticket.gate is not self))
        return asyncio.create_task(operation, name=name, context=context)

    async def operation(self, operation: Coroutine):
        lease = self._lease()
        if lease is not None and lease.active and lease.owner is asyncio.current_task():
            return await operation
        task = self.spawn(operation)
        # The actual task retains its lease after an HTTP caller disconnects.
        return await asyncio.shield(task)

    async def _maintain(self, operation, quiesce, outcome, control, expire_if_due):
        lease = None
        result, failure = None, None
        try:
            if expire_if_due():
                return
            if quiesce is not None:
                # Stop/settlement can deliberately suppress cancellation while
                # a real thread cleans up. A separate timer rejects the request
                # promptly without pretending that cleanup has already ended.
                await quiesce()
            while self._active and not expire_if_due():
                self._changed.clear()
                if self._active and not expire_if_due():
                    await self._changed.wait()
            process_maintenance = control["process_maintenance"]
            if process_maintenance is not None:
                while not expire_if_due() and not process_maintenance.try_exclusive():
                    await asyncio.sleep(.01)
            # A due timer callback can still be waiting for a loop turn after
            # synchronous quiesce/cleanup or a changed wakeup. Absolute time
            # must be checked even when no active lease forces another await.
            if expire_if_due():
                return
            control["started"] = True
            control["timer"].cancel()
            lease = _Lease(self, self._generation, "maintenance")
            result = await self._body(operation, lease)
        except ProcessGateError:
            failure = _conflict()
        except BaseException as error:
            failure = error
        finally:
            control["timer"].cancel()
            if control["process_maintenance"] is not None:
                try:
                    control["process_maintenance"].finish()
                except ProcessGateError:
                    failure = _conflict()
            if lease is None or not lease.started:
                self._close(operation)
            if lease is not None:
                lease.active = False
            self._generation += 1
            self._closing = False
            self._changed.set()
            if not outcome.done():
                if failure is not None:
                    outcome.set_exception(failure)
                elif control["started"]:
                    outcome.set_result(result)
                else:
                    outcome.set_exception(_conflict())

    async def maintenance(self, operation: Coroutine, *, quiesce: Callable | None = None):
        process_maintenance = None
        try:
            loop = self._check_loop()
            deadline = loop.time() + self._timeout
            lease, ticket = self._lease(), self._ticket()
            if (self._closed or self._closing or lease is not None or
                    ticket is not None and not self._ticket_is_current(ticket)):
                raise _conflict()
            if self._process_gate is not None:
                if ticket is not None and ticket.process_ticket is None:
                    raise _conflict()
                process_maintenance = self._process_gate.begin_maintenance(
                    expected=ticket.process_ticket if ticket is not None else None)
        except ProcessGateError as error:
            self._close(operation)
            raise _conflict() from error
        except BaseException:
            self._close(operation)
            raise
        self._closing = True
        self._generation += 1
        outcome = loop.create_future()
        # Observe an outcome even if its HTTP caller has disconnected.
        outcome.add_done_callback(lambda future: None if future.cancelled() else future.exception())
        control = {"aborted": False, "started": False, "timer": None,
            "process_maintenance": process_maintenance}

        def expire():
            if not control["started"]:
                control["aborted"] = True
                self._changed.set()
                if not outcome.done():
                    outcome.set_exception(_conflict())

        def expire_if_due():
            if loop.time() >= deadline:
                expire()
            return control["aborted"]

        control["timer"] = loop.call_at(deadline, expire)
        self._track(asyncio.create_task(self._maintain(operation, quiesce, outcome, control, expire_if_due)))
        return await asyncio.shield(outcome)

    async def shutdown(self):
        self._closed = True
        while self._tasks:
            await asyncio.gather(*(asyncio.shield(task) for task in tuple(self._tasks)), return_exceptions=True)
            # Completed tasks may still have their lease-release callbacks
            # queued. A gather of already-complete futures need not suspend.
            # Let those callbacks run before rechecking the tracking set.
            await asyncio.sleep(0)
