"""Strict ordinary preferences and device-local feedback contracts."""

from datetime import datetime
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

REQUEST_PATTERN = r"^[A-Za-z0-9_.:-]{1,64}$"
Kind = Literal["settings", "feedback"]
Category = Literal["device", "dialog", "motion", "account", "other"]


def visible(value, *, multiline=False):
    if not value.strip() or any((ord(c) < 32 and not (multiline and c in "\n\t"))
            or 127 <= ord(c) <= 159 or 0x202A <= ord(c) <= 0x202E
            or 0x2066 <= ord(c) <= 0x2069 or 0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise ValueError("Invalid preference text")
    value.encode("utf-8")
    return value


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Changes(Model):
    name: str | None = Field(default=None, min_length=1, max_length=20)
    volume: int | None = Field(default=None, ge=0, le=100)
    followUpSeconds: int | None = Field(default=None, ge=5, le=120)

    @model_validator(mode="after")
    def changed_fields(self):
        if not self.model_fields_set or any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError("At least one explicit non-null change is required")
        if self.name is not None:
            visible(self.name)
        return self


class Command(Model):
    requestId: str = Field(pattern=REQUEST_PATTERN)
    kind: Kind
    changes: Changes | None = None
    category: Category | None = None
    content: str | None = Field(default=None, min_length=2, max_length=500)

    @model_validator(mode="after")
    def fixed_kind(self):
        if self.kind == "settings":
            if self.changes is None or self.model_fields_set != {"requestId", "kind", "changes"}:
                raise ValueError("Settings accept only changes")
        else:
            if (self.category is None or self.content is None or
                    self.model_fields_set != {"requestId", "kind", "category", "content"}):
                raise ValueError("Feedback requires a fixed category and content")
            visible(self.content, multiline=True)
        return self


class Snapshot(Model):
    verified: bool
    settingsEnabled: bool
    feedbackEnabled: bool
    canChangeName: bool
    canChangeVolume: bool
    canChangeFollowUp: bool
    canSubmitFeedback: bool
    name: str | None = Field(min_length=1, max_length=20)
    storedVolume: int | None = Field(ge=0, le=100)
    outputVolume: int | None = Field(ge=0, le=150)
    outputMuted: bool | None
    outputVolumeAvailable: bool | None
    followUpSeconds: int | None = Field(ge=5, le=120)
    followUpSource: Literal["default", "persisted"] | None
    minimumSeconds: Literal[5] | None
    maximumSeconds: Literal[120] | None
    settingsReviewRequired: bool
    feedbackReviewRequired: bool
    manualReviewRequired: bool
    reason: str = Field(max_length=160)
    sampledAt: str


class Step(Model):
    step: Literal["pet_settings", "follow_up", "feedback"]
    status: Literal["pending", "sending", "applied", "not_sent", "unknown"]


class Receipt(Model):
    requestId: str = Field(pattern=REQUEST_PATTERN)
    kind: Kind
    status: Literal["sending", "recorded", "partially_applied", "not_sent", "unknown"]
    steps: list[Step] = Field(min_length=1, max_length=2)
    updatedAt: str


def validate_preferences(model, value):
    result = model.model_validate(value).model_dump()
    timestamp = result["sampledAt" if model is Snapshot else "updatedAt"]
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", timestamp) is None:
        raise ValueError("Invalid preferences timestamp")
    datetime.fromisoformat(timestamp[:-1] + "+00:00")
    if model is Snapshot:
        flags = [result[key] for key in ("canChangeName", "canChangeVolume", "canChangeFollowUp", "canSubmitFeedback")]
        if result["manualReviewRequired"] != (result["settingsReviewRequired"] or result["feedbackReviewRequired"]):
            raise ValueError("Invalid review policy")
        if any(flags[:3]) and not (result["verified"] and result["settingsEnabled"] and not result["settingsReviewRequired"]):
            raise ValueError("Invalid settings availability")
        if flags[3] and not (result["verified"] and result["feedbackEnabled"] and not result["feedbackReviewRequired"]):
            raise ValueError("Invalid feedback availability")
        fields = ("name", "storedVolume", "outputVolume", "outputMuted", "outputVolumeAvailable",
                  "followUpSeconds", "followUpSource", "minimumSeconds", "maximumSeconds")
        if not result["verified"] and (any(flags) or any(result[key] is not None for key in fields)):
            raise ValueError("Unverified preferences")
        if result["verified"]:
            if any(result[key] is None for key in ("name", "storedVolume", "followUpSeconds", "followUpSource", "minimumSeconds", "maximumSeconds")):
                raise ValueError("Incomplete preferences")
            visible(result["name"])
        if result["outputVolumeAvailable"] is not True and (result["outputVolume"] is not None or result["outputMuted"] is not None):
            raise ValueError("Unverified output volume")
        if flags[1] and result["outputVolumeAvailable"] is not True:
            raise ValueError("Unavailable output volume")
        if not any(flags) and not result["reason"]:
            raise ValueError("Missing preferences reason")
        if result["reason"]:
            visible(result["reason"])
    else:
        steps, states = [item["step"] for item in result["steps"]], [item["status"] for item in result["steps"]]
        if ((result["kind"] == "feedback" and steps != ["feedback"]) or
                (result["kind"] == "settings" and steps not in (["pet_settings"], ["follow_up"], ["pet_settings", "follow_up"]))):
            raise ValueError("Invalid preference steps")
        status = result["status"]
        if (status == "sending" and (not any(state in {"pending", "sending"} for state in states) or "unknown" in states)
                or status == "unknown" and ("unknown" not in states or any(state in {"pending", "sending"} for state in states))
                or status == "not_sent" and any(state != "not_sent" for state in states)
                or status == "recorded" and ("applied" not in states or any(state not in {"applied", "not_sent"} for state in states))
                or status == "partially_applied" and set(states) != {"applied", "not_sent"}):
            raise ValueError("Invalid preference receipt")
    return result
