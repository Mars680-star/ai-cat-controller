"""Opaque bearer values and their non-secret public/session representations."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import re


@dataclass(frozen=True)
class Session:
    user_id: str
    expires_at: int


@dataclass(frozen=True)
class IssuedSession(Session):
    access_token: str = field(repr=False)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def bearer_token(authorization: str) -> str | None:
    match = re.fullmatch(r"Bearer ([A-Za-z0-9_-]{32,256})", authorization)
    return match.group(1) if match else None


def utc_expiry(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace("+00:00", "Z")
