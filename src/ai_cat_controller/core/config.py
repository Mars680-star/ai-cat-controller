"""Environment-backed application settings."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

CONFIRMED_SERVICE_NAMES = frozenset(
    {
        "volc-pulseaudio.service",
        "volc-conv-ai.service",
        "volc-k1-wake-word.service",
    }
)


class Settings(BaseModel):
    """Validated runtime settings loaded without a dotenv side effect."""

    model_config = ConfigDict(frozen=True)

    hardware_driver: Literal["mock", "local_k1"] = "mock"
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    api_key_enabled: bool = False
    api_key: SecretStr = SecretStr("")

    command_timeout_seconds: float = Field(default=12.0, gt=0.0, le=60.0)
    motion_cooldown_seconds: float = Field(default=0.2, ge=0.0, le=10.0)
    service_status_cache_seconds: float = Field(default=2.0, ge=0.0, le=60.0)
    data_path: Path = Path(".data/ai-cat-mock.db")
    intimacy_daily_cap: int = Field(default=20, ge=1, le=100)

    hardware_binary: Path = Path("/usr/bin/ai-toy_app")
    systemctl_binary: Path = Path("/usr/bin/systemctl")
    dialog_status_path: Path = Path("/run/ai-cat/dialog-status.json")
    dialog_event_path: Path = Path("/var/lib/ai-cat-controller/dialog-events.jsonl")
    dialog_config_path: Path = Path(".data/dialog-runtime-config.json")
    battery_supply_path: Path = Path("/sys/class/power_supply/cw-bat")
    charger_supply_path: Path = Path("/sys/class/power_supply/ip2317-charger")
    dialog_service: str = "volc-conv-ai.service"
    wake_service: str = "volc-k1-wake-word.service"
    pulseaudio_service: str = "volc-pulseaudio.service"

    @field_validator("api_host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("AI_CAT_API_HOST must not be empty")
        return value

    @field_validator("dialog_service", "wake_service", "pulseaudio_service")
    @classmethod
    def validate_service_name(cls, value: str) -> str:
        if value not in CONFIRMED_SERVICE_NAMES:
            raise ValueError(f"unconfirmed systemd service: {value}")
        return value

    @field_validator("systemctl_binary")
    @classmethod
    def validate_systemctl_binary(cls, value: Path) -> Path:
        if str(value) not in {"/usr/bin/systemctl", "/bin/systemctl"}:
            raise ValueError("AI_CAT_SYSTEMCTL_BINARY is not allowlisted")
        return value

    @field_validator("hardware_binary")
    @classmethod
    def validate_hardware_binary(cls, value: Path) -> Path:
        if str(value) != "/usr/bin/ai-toy_app":
            raise ValueError("AI_CAT_HARDWARE_BINARY is not allowlisted")
        return value

    @model_validator(mode="after")
    def validate_security(self) -> "Settings":
        key = self.api_key.get_secret_value()
        if self.api_key_enabled and not key:
            raise ValueError("AI_CAT_API_KEY is required when API key authentication is enabled")
        if self.hardware_driver == "local_k1" and not self.api_key_enabled:
            raise ValueError("local_k1 mode requires AI_CAT_API_KEY_ENABLED=true")
        return self

    @property
    def service_names(self) -> tuple[str, str, str]:
        return (self.pulseaudio_service, self.dialog_service, self.wake_service)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Settings":
        source = os.environ if environ is None else environ
        values = {
            "hardware_driver": source.get("AI_CAT_HARDWARE_DRIVER", "mock"),
            "api_host": source.get("AI_CAT_API_HOST", "127.0.0.1"),
            "api_port": source.get("AI_CAT_API_PORT", "8000"),
            "log_level": source.get("AI_CAT_LOG_LEVEL", "INFO").upper(),
            "api_key_enabled": source.get("AI_CAT_API_KEY_ENABLED", "false"),
            "api_key": source.get("AI_CAT_API_KEY", ""),
            "command_timeout_seconds": source.get("AI_CAT_COMMAND_TIMEOUT_SECONDS", "12.0"),
            "motion_cooldown_seconds": source.get("AI_CAT_MOTION_COOLDOWN_SECONDS", "0.2"),
            "service_status_cache_seconds": source.get(
                "AI_CAT_SERVICE_STATUS_CACHE_SECONDS", "2.0"
            ),
            "data_path": source.get("AI_CAT_DATA_PATH", ".data/ai-cat-mock.db"),
            "intimacy_daily_cap": source.get("AI_CAT_INTIMACY_DAILY_CAP", "20"),
            "hardware_binary": source.get("AI_CAT_HARDWARE_BINARY", "/usr/bin/ai-toy_app"),
            "systemctl_binary": source.get("AI_CAT_SYSTEMCTL_BINARY", "/usr/bin/systemctl"),
            "dialog_status_path": source.get(
                "AI_CAT_DIALOG_STATUS_PATH", "/run/ai-cat/dialog-status.json"
            ),
            "dialog_event_path": source.get(
                "AI_CAT_DIALOG_EVENT_PATH",
                "/var/lib/ai-cat-controller/dialog-events.jsonl",
            ),
            "dialog_config_path": source.get(
                "AI_CAT_DIALOG_CONFIG_PATH",
                ".data/dialog-runtime-config.json",
            ),
            "battery_supply_path": source.get(
                "AI_CAT_BATTERY_SUPPLY_PATH",
                "/sys/class/power_supply/cw-bat",
            ),
            "charger_supply_path": source.get(
                "AI_CAT_CHARGER_SUPPLY_PATH",
                "/sys/class/power_supply/ip2317-charger",
            ),
            "dialog_service": source.get(
                "AI_CAT_DIALOG_SERVICE", "volc-conv-ai.service"
            ),
            "wake_service": source.get(
                "AI_CAT_WAKE_SERVICE", "volc-k1-wake-word.service"
            ),
            "pulseaudio_service": source.get(
                "AI_CAT_PULSEAUDIO_SERVICE", "volc-pulseaudio.service"
            ),
        }
        return cls.model_validate(values)
