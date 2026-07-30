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
