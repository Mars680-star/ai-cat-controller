"""Explicit owner enrollment for a private on-device control sidecar."""
import asyncio
import secrets
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ai_cat_controller.api.dependencies import get_services
from ai_cat_controller.core.security import require_api_key
from ai_cat_controller.core.state import AppServices
from ai_cat_controller.services.personality_service import PersonalityService

router = APIRouter(prefix='/api/v1/local-gateway',tags=['本机网关'],dependencies=[Depends(require_api_key)])


class _Enrollment(BaseModel):
    model_config = ConfigDict(extra='forbid',strict=True)
    pet_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,128}$')
    device_serial: str = Field(pattern=r'^[A-Za-z0-9_-]{6,64}$')
    confirmation: Literal['install-local-gateway']


def _reply(status, data=None):
    return JSONResponse({'success':status==200, **({'data':data} if data is not None else
        {'message':'本机网关授权不可用，请核对原设备和当前归属。'})},status_code=status,
        headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})


@router.post('/delegations')
async def enroll(payload:_Enrollment, request:Request, services:Annotated[AppServices,Depends(get_services)]):
    settings = services.settings
    if not settings.enable_local_gateway_delegation:
        return _reply(404)
    if (request.client is None or request.client.host not in {'127.0.0.1','::1'} or
            request.url.query or request.headers.getlist('x-local-gateway') or
            len(request.headers.getlist('x-mock-session')) != 1 or
            len(request.headers.getlist('x-api-key')) != 1):
        return _reply(403)
    try:
        user = services.product.resolve_session(request.headers['x-mock-session'])
        # Match issuance to one real binding; never create users/pets or silently
        # adopt a default device. Lifecycle mutation cannot interleave issuance.
        async with services.product._lifecycle_lock:
            serial = await asyncio.to_thread(PersonalityService.read_device_serial,settings.device_serial_path)
            if not serial or not secrets.compare_digest(serial,payload.device_serial):
                return _reply(403)
            binding = await asyncio.to_thread(services.repository.get_owned_pet,user,payload.pet_id,read_only=True)
            if not secrets.compare_digest(binding['serial_number'],serial):
                return _reply(403)
            token = await asyncio.to_thread(services.local_gateway_delegations.issue,binding)
        return _reply(200,dict(delegation_token=token,pet_id=binding['pet_id'],device_serial=serial,
            owner_user_id=user,binding_epoch=binding['bound_at']))
    except Exception:
        return _reply(403)
