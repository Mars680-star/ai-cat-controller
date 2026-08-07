"""API-key authentication dependency."""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Header, Request

from ai_cat_controller.core.errors import AuthenticationError


async def require_api_key(
    request: Request,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    settings = request.app.state.services.settings
    if not settings.api_key_enabled:
        return

    expected = settings.api_key.get_secret_value()
    if x_api_key is None or not secrets.compare_digest(x_api_key, expected):
        raise AuthenticationError("API Key 缺失或无效")
