"""Local service status endpoint."""

from fastapi import APIRouter, Depends

from ai_cat_controller.api.dependencies import get_services
from ai_cat_controller.core.state import AppServices
from ai_cat_controller.schemas.common import ApiResponse
from ai_cat_controller.schemas.device import ServiceStatusData, ServicesStatusData

router = APIRouter(tags=["服务"])


@router.get(
    "/services/status",
    response_model=ApiResponse[ServicesStatusData],
    summary="获取本地服务状态",
    description="Mock 模式返回模拟状态；Local K1 仅执行固定服务的只读状态查询。",
)
async def services_status(
    services: AppServices = Depends(get_services),
) -> ApiResponse[ServicesStatusData]:
    statuses = await services.device.get_services_status()
    return ApiResponse(
        message="服务状态获取成功",
        data=ServicesStatusData(
            services=[ServiceStatusData.model_validate(item) for item in statuses]
        ),
    )
