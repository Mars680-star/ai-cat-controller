"""Domain errors and their HTTP status mapping."""

from __future__ import annotations

from typing import Any


class AiCatError(Exception):
    status_code = 500
    code = "ai_cat_error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class AuthenticationError(AiCatError):
    status_code = 401
    code = "authentication_failed"


class AdapterNotImplementedError(AiCatError):
    status_code = 501
    code = "adapter_not_implemented"


class DeviceUnavailableError(AiCatError):
    status_code = 503
    code = "device_unavailable"


class ActionConflictError(AiCatError):
    status_code = 409
    code = "action_conflict"


class ActionTimeoutError(AiCatError):
    status_code = 504
    code = "action_timeout"


class CommandNotAllowedError(AiCatError):
    status_code = 501
    code = "command_not_allowed"
