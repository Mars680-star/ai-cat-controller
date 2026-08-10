"""Request models for the product-experience Mock API."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


def _request_id() -> str:
    return f"req_{uuid.uuid4().hex}"


class MockLoginRequest(BaseModel):
    login_code: str = Field(min_length=3, max_length=64)
    nickname: str = Field(default="体验用户", min_length=1, max_length=32)


class BindPetRequest(BaseModel):
    device_serial: str = Field(min_length=4, max_length=64)
    pet_name: str = Field(default="小安", min_length=1, max_length=20)
    network_name: str = Field(default="Mock Wi-Fi", min_length=1, max_length=64)


class InteractionRequest(BaseModel):
    event_type: Literal[
        "daily_check_in",
        "valid_dialog",
        "touch",
        "completed_task",
        "ignored_greeting",
    ]
    request_id: str = Field(default_factory=_request_id, min_length=4, max_length=80)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecuteActionRequest(BaseModel):
    request_id: str = Field(default_factory=_request_id, min_length=4, max_length=80)


class SendDialogRequest(BaseModel):
    content: str = Field(min_length=1, max_length=500)
    trigger_action: bool = True


class PetSettingsRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=20)
    volume: int | None = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def validate_change(self) -> "PetSettingsRequest":
        if self.name is None and self.volume is None:
            raise ValueError("name or volume is required")
        return self


class FeedbackRequest(BaseModel):
    category: Literal["device", "dialog", "motion", "account", "other"] = "other"
    content: str = Field(min_length=2, max_length=500)


class MockDeviceStatusRequest(BaseModel):
    online: bool
    battery_percent: int = Field(ge=0, le=100)
    charging: bool
    network_status: Literal["online", "weak", "offline"]


class ProductDataResetRequest(BaseModel):
    confirmation: Literal["RESET_PRODUCT_DATA"]


class DebugGrowthRequest(BaseModel):
    request_id: str = Field(default_factory=_request_id, min_length=4, max_length=80)
    event_type: Literal[
        "knowledge_discussion",
        "question",
        "emotional_sharing",
        "joke",
        "casual_chat",
        "planning",
        "encouragement",
        "physical_touch",
        "completed_task",
        "daily_meeting",
    ]
    count: int = Field(default=1, ge=1, le=500)
    topic: str = Field(default="debug", min_length=1, max_length=64)
    emotion: Literal[
        "positive",
        "neutral",
        "negative",
        "excited",
        "sad",
        "angry",
    ] = "neutral"
    engagement: float = Field(default=1.0, ge=0.0, le=1.0)
