"""Fixed dialog intents; command bodies are never public receipts."""

from datetime import datetime
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .dialog_models import State

REQUEST_PATTERN = r"^[A-Za-z0-9_.:-]{1,64}$"
Kind = Literal["listen", "interrupt", "text"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Command(Model):
    requestId: str = Field(pattern=REQUEST_PATTERN)
    kind: Kind
    content: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_content(self):
        if self.kind != "text":
            if "content" in self.model_fields_set:
                raise ValueError("Only text commands may contain content")
        else:
            if self.content is None or not self.content.strip():
                raise ValueError("Text content must not be blank")
            if any(ord(c) < 32 or 127 <= ord(c) <= 159 or 0x202A <= ord(c) <= 0x202E
                   or 0x2066 <= ord(c) <= 0x2069 or 0xD800 <= ord(c) <= 0xDFFF for c in self.content):
                raise ValueError("Invalid text characters")
            self.content.encode("utf-8")
        return self


class Control(Model):
    enabled: bool
    verified: bool
    canListen: bool
    canInterrupt: bool
    canSendText: bool
    manualReviewRequired: bool
    reason: str = Field(max_length=160)
    state: State
    stale: bool
    sampledAt: str


class Receipt(Model):
    requestId: str = Field(pattern=REQUEST_PATTERN)
    kind: Kind
    status: Literal["sending", "accepted", "not_sent", "unknown"]
    updatedAt: str


def validate_control(model, value):
    result = model.model_validate(value).model_dump()
    key = "sampledAt" if model is Control else "updatedAt"
    timestamp = result[key]
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", timestamp) is None:
        raise ValueError("Invalid dialog control time")
    datetime.fromisoformat(timestamp[:-1] + "+00:00")
    if model is Control:
        flags = (result["canListen"], result["canInterrupt"], result["canSendText"])
        if any(flags) and not (result["enabled"] and result["verified"]):
            raise ValueError("Invalid dialog control availability")
        if (result["stale"] or result["manualReviewRequired"] or result["state"] == "unavailable") and (flags[0] or flags[2]):
            raise ValueError("Invalid normal command availability")
        if not any(flags) and not result["reason"]:
            raise ValueError("Missing dialog control reason")
        if any(ord(c) < 32 or 127 <= ord(c) <= 159 or 0x202A <= ord(c) <= 0x202E
               or 0x2066 <= ord(c) <= 0x2069 for c in result["reason"]):
            raise ValueError("Invalid dialog control reason")
    return result
