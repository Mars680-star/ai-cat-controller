"""Adapter contract shared by Mock and K1 implementations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any


class Capability(str, Enum):
    BATTERY_STATUS = "battery_status"
    SHAKE_HEAD = "shake_head"
    NOD_HEAD = "nod_head"
    WAG_TAIL = "wag_tail"
    STOP_MOTION = "stop_motion"
    WAKE_DIALOG = "wake_dialog"
    INTERRUPT_DIALOG = "interrupt_dialog"


class AiCatAdapter(ABC):
    mode: str
    capabilities: frozenset[Capability] = frozenset()

    def supports(self, capability: Capability) -> bool:
        return capability in self.capabilities

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    @abstractmethod
    async def get_device_status(self) -> dict[str, Any]: ...

    @abstractmethod
    async def get_services_status(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def get_dialog_status(self) -> dict[str, Any]: ...

    @abstractmethod
    async def shake_head(self, intensity: float, duration_ms: int) -> None: ...

    @abstractmethod
    async def nod_head(self, intensity: float, duration_ms: int) -> None: ...

    @abstractmethod
    async def wag_tail(self, intensity: float, duration_ms: int) -> None: ...

    @abstractmethod
    async def stop_motion(self) -> None: ...

    @abstractmethod
    async def wake_dialog(self) -> None: ...

    @abstractmethod
    async def interrupt_dialog(self) -> None: ...
