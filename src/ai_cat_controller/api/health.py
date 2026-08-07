"""Process health endpoint."""

from fastapi import APIRouter, Depends

from ai_cat_controller import __version__
from ai_cat_controller.api.dependencies import get_services
from ai_cat_controller.core.state import AppServices
from ai_cat_controller.schemas.common import ApiResponse, HealthData

router = APIRouter(tags=["健康检查"])


@router.get(
    "/health",
    response_model=ApiResponse[HealthData],
    summary="检查控制服务",
    description="只检查 FastAPI 进程是否正常，不依赖真实硬件状态，也不要求 API Key。",
)
async def health(
    services: AppServices = Depends(get_services),
) -> ApiResponse[HealthData]:
    return ApiResponse(
        message="AI Cat Controller is running",
        data=HealthData(
            status="ok",
            version=__version__,
            adapter=services.settings.hardware_driver,
        ),
    )
