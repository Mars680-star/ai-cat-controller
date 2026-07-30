"""Device status endpoint."""

from fastapi import APIRouter, Depends

from ai_cat_controller.api.dependencies import get_services
from ai_cat_controller.core.state import AppServices
from ai_cat_controller.schemas.common import ApiResponse
from ai_cat_controller.schemas.device import DeviceStatusData

router = APIRouter(tags=["设备"])


@router.get(
    "/device/status",
    response_model=ApiResponse[DeviceStatusData],
    summary="获取设备状态",
    description="返回适配器、动作、对话和控制服务运行状态，不返回密钥或环境变量。",
)
async def device_status(
    services: AppServices = Depends(get_services),
) -> ApiResponse[DeviceStatusData]:
    status = await services.device.get_device_status()
    return ApiResponse(
        message="设备状态获取成功",
        data=DeviceStatusData.model_validate(status),
    )
