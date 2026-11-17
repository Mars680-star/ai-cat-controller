"""Explicit, secret-safe configuration for the independent mini-program gateway."""

from dataclasses import dataclass, field
from pathlib import Path
import re


@dataclass(frozen=True)
class MiniGatewayConfig:
    db_path: Path
    app_id: str = field(default="", repr=False)
    app_secret: str = field(default="", repr=False)
    session_ttl_seconds: int = 3600
    status_pet_id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "db_path", Path(self.db_path))
        if type(self.session_ttl_seconds) is not int or not 60 <= self.session_ttl_seconds <= 86400:
            raise ValueError("Invalid gateway session lifetime")
        for value in (self.app_id, self.app_secret):
            if not isinstance(value, str) or len(value) > 256 or any(ord(c) < 33 or ord(c) > 126 for c in value):
                raise ValueError("Invalid WeChat configuration")
        if self.app_id and not re.fullmatch(r"wx[0-9a-fA-F]{16}", self.app_id):
            raise ValueError("Invalid WeChat configuration")
        if self.app_secret and not re.fullmatch(r"[0-9a-fA-F]{32}", self.app_secret):
            raise ValueError("Invalid WeChat configuration")
        if not isinstance(self.status_pet_id, str) or (self.status_pet_id and not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", self.status_pet_id)):
            raise ValueError("Invalid status pet mapping")

    @property
    def wechat_configured(self) -> bool:
        return bool(self.app_id and self.app_secret)
