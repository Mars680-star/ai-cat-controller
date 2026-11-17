"""Strict request contracts for the future Mi Home gateway boundary."""

from __future__ import annotations

from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator

MijiaPropertyId: TypeAlias = Literal[
    "online",
    "battery_level",
    "charging",
    "volume",
    "intimacy_level",
    "intimacy_points",
    "dialog_state",
    "motion_state",
]

MijiaActionId: TypeAlias = Literal[
    "head_nod",
    "head_shake",
    "tail_wag",
    "stop_motion",
    "start_listening",
    "interrupt_dialog",
]


class _StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MijiaIntegrationRequest(_StrictRequest):
    request_id: str = Field(min_length=4, max_length=80)


class MijiaPropertyReadRequest(_StrictRequest):
    properties: list[MijiaPropertyId] = Field(min_length=1, max_length=8)

    @field_validator("properties")
    @classmethod
    def unique_properties(
        cls,
        properties: list[MijiaPropertyId],
    ) -> list[MijiaPropertyId]:
        if len(properties) != len(set(properties)):
            raise ValueError("properties must not contain duplicates")
        return properties


class MijiaPropertyWriteRequest(_StrictRequest):
    property_id: Literal["volume"]
    value: int = Field(ge=0, le=100)
    request_id: str = Field(min_length=4, max_length=80)


class MijiaActionRequest(_StrictRequest):
    request_id: str = Field(min_length=4, max_length=80)
