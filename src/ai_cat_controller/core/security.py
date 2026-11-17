"""API-key authentication dependency."""

from __future__ import annotations

import secrets
import asyncio
from typing import Annotated

from fastapi import Header, Request

from ai_cat_controller.core.errors import AuthenticationError
from ai_cat_controller.services.personality_service import PersonalityService


def resolve_local_delegation(services, token, method, path):
    store, repository = services.local_gateway_delegations, services.repository
    if store is None or repository is None:
        raise ValueError('Local delegation unavailable')
    serial = PersonalityService.read_device_serial(services.settings.device_serial_path)
    if not serial:
        raise ValueError('Local identity unavailable')
    binding = repository.get_pet_by_serial(serial)
    return store.resolve(token, binding, method, path)


async def require_api_key(
    request: Request,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    settings = request.app.state.services.settings
    grants = request.headers.getlist('x-local-gateway')
    if grants and (len(grants) != 1 or request.headers.getlist('x-mock-session') or
                   len(request.headers.getlist('x-api-key')) != 1):
        raise AuthenticationError('本机网关授权无效')
    if not settings.api_key_enabled:
        if grants:
            raise AuthenticationError('本机网关授权未启用')
        return

    expected = settings.api_key.get_secret_value()
    if x_api_key is None or not secrets.compare_digest(x_api_key, expected):
        raise AuthenticationError("API Key 缺失或无效")
    if grants:
        if (not settings.enable_local_gateway_delegation or request.client is None or
                request.client.host not in {'127.0.0.1','::1'} or request.url.query):
            raise AuthenticationError('本机网关授权无效')
        try:
            principal = await asyncio.to_thread(resolve_local_delegation,request.app.state.services,
                grants[0],request.method,request.url.path)
        except Exception:
            raise AuthenticationError('本机网关授权无效') from None
        request.state.local_gateway_principal = principal
