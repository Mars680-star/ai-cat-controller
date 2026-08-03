"""Dialog control models."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class DialogRequest(BaseModel):
    request_id: str | None = Field(default=None, max_length=64)

    @field_validator("request_id")
    @classmethod
    def validate_request_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("request_id must not be blank")
        if not all(character.isalnum() or character in "._:-" for character in value):
            raise ValueError("request_id contains unsupported characters")
        return value


class DialogActionData(BaseModel):
    dialog_state: str
    request_id: str | None
    changed: bool


class DialogTextRequest(DialogRequest):
    content: str = Field(min_length=1, max_length=500)

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("content must not be blank")
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("content contains unsupported control characters")
        return value


class DialogSpeakRequest(DialogTextRequest):
    content: str = Field(min_length=1, max_length=100)


class DialogStatusData(BaseModel):
    state: str
    message: str
    session_active: bool
    can_interrupt: bool
    follow_up_deadline_ms: int
    updated_at_ms: int
    sequence: int
    source: str
    stale: bool


class DialogConfigUpdate(BaseModel):
    follow_up_seconds: int = Field(ge=5, le=120)


class DialogConfigData(BaseModel):
    follow_up_seconds: int = Field(ge=5, le=120)
    minimum_seconds: int = 5
    maximum_seconds: int = 120
    source: str
