"""Shared API envelopes."""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

DataT = TypeVar("DataT")


class ApiResponse(BaseModel, Generic[DataT]):
    success: bool = True
    message: str
    data: DataT


class ErrorData(BaseModel):
    code: str
    details: Any = Field(default_factory=dict)


class HealthData(BaseModel):
    status: str
    version: str
    adapter: str
