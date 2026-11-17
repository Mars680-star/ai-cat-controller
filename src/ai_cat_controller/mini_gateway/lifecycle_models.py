"""Bounded lifecycle contracts. Whole-device reset is never a pet-only reset."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Kind = Literal['memory_clear', 'unbind', 'product_reset']
Scope = Literal['owner_pet_memory', 'owner_pet_binding', 'whole_device_product_data']
SCOPES = {'memory_clear': 'owner_pet_memory', 'unbind': 'owner_pet_binding',
          'product_reset': 'whole_device_product_data'}
REQUEST_PATTERN = r'^[A-Za-z0-9_.:-]{1,64}$'


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class ScopeRequest(Model):
    kind: Kind
    scope: Scope

    @model_validator(mode='after')
    def actual_scope(self):
        if SCOPES[self.kind] != self.scope:
            raise ValueError('Operation and data scope disagree')
        return self


class Command(ScopeRequest):
    requestId: str = Field(pattern=REQUEST_PATTERN)
    challengeId: str = Field(pattern=r'^[A-Za-z0-9_-]{43}$')
    confirmVersion: int = Field(strict=True, ge=1, le=1)


class Operation(ScopeRequest):
    enabled: bool
    available: bool
    operatorRequired: bool


class Snapshot(Model):
    verified: bool
    operations: list[Operation] = Field(min_length=3, max_length=3)
    manualReviewRequired: bool
    reason: str = Field(max_length=160)
    sampledAt: str

    @model_validator(mode='after')
    def gates(self):
        if {operation.kind for operation in self.operations} != set(SCOPES):
            raise ValueError('Missing lifecycle operation')
        if any(operation.available and not (self.verified and operation.enabled and not self.manualReviewRequired)
               for operation in self.operations):
            raise ValueError('Unavailable lifecycle operation')
        if any(operation.operatorRequired != (operation.kind == 'product_reset') for operation in self.operations):
            raise ValueError('Invalid operator requirement')
        return self


class Challenge(ScopeRequest):
    challengeId: str = Field(pattern=r'^[A-Za-z0-9_-]{43}$')
    confirmVersion: int = Field(strict=True, ge=1, le=1)
    expiresAt: str


class Receipt(ScopeRequest):
    requestId: str = Field(pattern=REQUEST_PATTERN)
    status: Literal['sending', 'completed', 'not_sent', 'unknown']
    updatedAt: str


def validate_lifecycle(model, value):
    result = model.model_validate(value).model_dump()
    for field in ('sampledAt', 'expiresAt', 'updatedAt'):
        if field in result:
            raw = result[field]
            if not isinstance(raw, str) or not raw.endswith('Z'):
                raise ValueError('UTC lifecycle timestamp required')
            parsed = datetime.fromisoformat(raw[:-1] + '+00:00')
            if parsed.utcoffset().total_seconds() != 0:
                raise ValueError('UTC lifecycle timestamp required')
    return result
