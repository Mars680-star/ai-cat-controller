"""Motion request and response models."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class MotionRequest(BaseModel):
    intensity: float = Field(default=0.5, ge=0.1, le=1.0)
    duration_ms: int = Field(default=600, ge=100, le=3000)
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


class MotionAcceptedData(BaseModel):
    action: str
    request_id: str | None
    state: str


class MotionStopData(BaseModel):
    stopped: bool
    state: str
