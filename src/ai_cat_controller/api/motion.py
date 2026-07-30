"""Motion scheduling endpoints."""

from fastapi import APIRouter, Depends, status

from ai_cat_controller.api.dependencies import get_services
from ai_cat_controller.core.state import AppServices
from ai_cat_controller.schemas.common import ApiResponse
from ai_cat_controller.schemas.motion import (
    MotionAcceptedData,
    MotionRequest,
    MotionStopData,
)

router = APIRouter(prefix="/motion", tags=["动作"])


def _accepted(data: dict[str, object]) -> ApiResponse[MotionAcceptedData]:
    return ApiResponse(
        message="动作已接受",
        data=MotionAcceptedData.model_validate(data),
    )


@router.post(
    "/head/shake",
    response_model=ApiResponse[MotionAcceptedData],
    status_code=status.HTTP_202_ACCEPTED,
    summary="摇头",
    description="提交固定的摇头动作。第一阶段仅 Mock 适配器实现真实执行。",
)
async def shake_head(
    request: MotionRequest,
    services: AppServices = Depends(get_services),
) -> ApiResponse[MotionAcceptedData]:
    return _accepted(
        await services.motion.shake_head(
            request.intensity, request.duration_ms, request.request_id
        )
    )


@router.post(
    "/head/nod",
    response_model=ApiResponse[MotionAcceptedData],
    status_code=status.HTTP_202_ACCEPTED,
    summary="点头",
    description="提交固定的点头动作。第一阶段仅 Mock 适配器实现真实执行。",
)
async def nod_head(
    request: MotionRequest,
    services: AppServices = Depends(get_services),
) -> ApiResponse[MotionAcceptedData]:
    return _accepted(
        await services.motion.nod_head(
            request.intensity, request.duration_ms, request.request_id
        )
    )


@router.post(
    "/tail/wag",
    response_model=ApiResponse[MotionAcceptedData],
    status_code=status.HTTP_202_ACCEPTED,
    summary="摇尾",
    description="提交固定的摇尾动作。第一阶段仅 Mock 适配器实现真实执行。",
)
async def wag_tail(
    request: MotionRequest,
    services: AppServices = Depends(get_services),
) -> ApiResponse[MotionAcceptedData]:
    return _accepted(
        await services.motion.wag_tail(
            request.intensity, request.duration_ms, request.request_id
        )
    )


@router.post(
    "/stop",
    response_model=ApiResponse[MotionStopData],
    summary="停止当前动作",
    description="取消正在执行的动作；没有动作时返回幂等成功。",
)
async def stop_motion(
    services: AppServices = Depends(get_services),
) -> ApiResponse[MotionStopData]:
    result = await services.motion.stop()
    return ApiResponse(
        message="动作已停止" if result["stopped"] else "当前没有运行中的动作",
        data=MotionStopData.model_validate(result),
    )
