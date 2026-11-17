"""Mi Home preparation UI routes and the allowlisted gateway boundary."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, status

from ai_cat_controller.api.dependencies import get_services
from ai_cat_controller.api.product import mock_user_id
from ai_cat_controller.core.state import AppServices
from ai_cat_controller.schemas.common import ApiResponse
from ai_cat_controller.schemas.mijia import (
    MijiaActionId,
    MijiaActionRequest,
    MijiaIntegrationRequest,
    MijiaPropertyReadRequest,
    MijiaPropertyWriteRequest,
)

router = APIRouter(tags=["米家接入"])


def _response(message: str, data: Any) -> ApiResponse[Any]:
    return ApiResponse(message=message, data=data)


@router.get(
    "/integrations/mijia/pets/{pet_id}/status",
    response_model=ApiResponse[dict[str, Any]],
)
async def integration_status(
    pet_id: str,
    services: Annotated[AppServices, Depends(get_services)],
    user_id: Annotated[str, Depends(mock_user_id)],
) -> ApiResponse[Any]:
    return _response("米家接入准备状态", await services.mijia.status(user_id, pet_id))


@router.get(
    "/integrations/mijia/pets/{pet_id}/spec",
    response_model=ApiResponse[dict[str, Any]],
)
async def integration_spec(
    pet_id: str,
    services: Annotated[AppServices, Depends(get_services)],
    user_id: Annotated[str, Depends(mock_user_id)],
) -> ApiResponse[Any]:
    return _response("米家能力草案", await services.mijia.spec(user_id, pet_id))


@router.post(
    "/integrations/mijia/pets/{pet_id}/prepare",
    response_model=ApiResponse[dict[str, Any]],
)
async def prepare_integration(
    pet_id: str,
    request: MijiaIntegrationRequest,
    services: Annotated[AppServices, Depends(get_services)],
    user_id: Annotated[str, Depends(mock_user_id)],
) -> ApiResponse[Any]:
    result = await services.mijia.prepare(user_id, pet_id, request.request_id)
    return _response("米家接入资料已准备", result)


@router.post(
    "/integrations/mijia/pets/{pet_id}/remove",
    response_model=ApiResponse[dict[str, Any]],
)
async def remove_integration(
    pet_id: str,
    request: MijiaIntegrationRequest,
    services: Annotated[AppServices, Depends(get_services)],
    user_id: Annotated[str, Depends(mock_user_id)],
) -> ApiResponse[Any]:
    result = await services.mijia.remove(user_id, pet_id, request.request_id)
    return _response("米家接入准备已清除", result)


@router.get(
    "/gateway/mijia/v1/devices/{gateway_device_id}/snapshot",
    response_model=ApiResponse[dict[str, Any]],
)
async def gateway_snapshot(
    gateway_device_id: str,
    services: Annotated[AppServices, Depends(get_services)],
) -> ApiResponse[Any]:
    return _response("设备快照", await services.mijia.snapshot(gateway_device_id))


@router.post(
    "/gateway/mijia/v1/devices/{gateway_device_id}/properties/read",
    response_model=ApiResponse[dict[str, Any]],
)
async def read_properties(
    gateway_device_id: str,
    request: MijiaPropertyReadRequest,
    services: Annotated[AppServices, Depends(get_services)],
) -> ApiResponse[Any]:
    result = await services.mijia.read_properties(
        gateway_device_id,
        request.properties,
    )
    return _response("属性读取完成", result)


@router.post(
    "/gateway/mijia/v1/devices/{gateway_device_id}/properties/write",
    response_model=ApiResponse[dict[str, Any]],
)
async def write_property(
    gateway_device_id: str,
    request: MijiaPropertyWriteRequest,
    services: Annotated[AppServices, Depends(get_services)],
) -> ApiResponse[Any]:
    result = await services.mijia.write_volume(
        gateway_device_id,
        value=request.value,
        request_id=request.request_id,
    )
    return _response("属性写入完成", result)


@router.post(
    "/gateway/mijia/v1/devices/{gateway_device_id}/actions/{action_id}",
    response_model=ApiResponse[dict[str, Any]],
    status_code=status.HTTP_202_ACCEPTED,
)
async def invoke_action(
    gateway_device_id: str,
    action_id: MijiaActionId,
    request: MijiaActionRequest,
    services: Annotated[AppServices, Depends(get_services)],
) -> ApiResponse[Any]:
    result = await services.mijia.invoke_action(
        gateway_device_id,
        action_id,
        request.request_id,
    )
    return _response("动作请求已处理", result)
