"""Device and service status models."""

from __future__ import annotations

from pydantic import BaseModel


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


class ServiceStatusData(BaseModel):
    service_name: str
    active: bool
    enabled: bool
    error: str | None


class ServicesStatusData(BaseModel):
    services: list[ServiceStatusData]
