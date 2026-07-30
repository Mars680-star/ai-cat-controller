"""Dialog control endpoints."""

from fastapi import APIRouter, Depends

from ai_cat_controller.api.dependencies import get_services
from ai_cat_controller.core.state import AppServices
from ai_cat_controller.schemas.common import ApiResponse
from ai_cat_controller.schemas.dialog import (
    DialogActionData,
    DialogRequest,
    DialogStatusData,
)

router = APIRouter(prefix="/dialog", tags=["对话"])


@router.get(
    "/status",
    response_model=ApiResponse[DialogStatusData],
    summary="获取实时对话状态",
)
async def dialog_status(
    services: AppServices = Depends(get_services),
) -> ApiResponse[DialogStatusData]:
    result = await services.dialog.get_status()
    return ApiResponse(
        message="对话状态获取成功",
        data=DialogStatusData.model_validate(result),
    )


@router.post(
    "/wake",
    response_model=ApiResponse[DialogActionData],
    summary="唤醒对话",
    description="开始连续对话；回答期间再次调用会打断并继续聆听。",
)
async def wake_dialog(
    request: DialogRequest | None = None,
    services: AppServices = Depends(get_services),
) -> ApiResponse[DialogActionData]:
    result = await services.dialog.wake(request.request_id if request else None)
    return ApiResponse(
        message="已请求开始聆听" if result["changed"] else "唤醒请求正在处理中",
        data=DialogActionData.model_validate(result),
    )


@router.post(
    "/interrupt",
    response_model=ApiResponse[DialogActionData],
    summary="打断对话",
    description="只打断当前回答并结束连续对话，不自动开始下一轮。",
)
async def interrupt_dialog(
    request: DialogRequest | None = None,
    services: AppServices = Depends(get_services),
) -> ApiResponse[DialogActionData]:
    result = await services.dialog.interrupt(request.request_id if request else None)
    return ApiResponse(
        message="对话已打断",
        data=DialogActionData.model_validate(result),
    )
