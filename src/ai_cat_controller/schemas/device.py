"""Device and service status models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class DeviceStatusData(BaseModel):
    adapter_mode: str
    connected: bool
    hostname: str
    platform: str
    python_version: str
    current_action: str
    last_action: str | None
    dialog_state: str
    head_state: str
    tail_state: str
    action_count: int
    last_action_at: str | None
    last_error: str | None
    adapter_uptime_seconds: float
    uptime_seconds: float
    battery_available: bool
    battery_percent: int | None = Field(default=None, ge=0, le=100)
    battery_status: Literal[
        "charging",
        "discharging",
        "full",
        "not_charging",
        "unknown",
        "unavailable",
    ]
    battery_present: bool | None
    battery_voltage_mv: int | None = Field(default=None, ge=0, le=20_000)
    charging: bool | None
    charger_online: bool | None
    battery_error: str | None


class ServiceStatusData(BaseModel):
    service_name: str
    active: bool
    enabled: bool
    error: str | None


class ServicesStatusData(BaseModel):
    services: list[ServiceStatusData]
