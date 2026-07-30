"""Dialog control endpoints."""

from fastapi import APIRouter, Depends

from ai_cat_controller.api.dependencies import get_services
from ai_cat_controller.core.state import AppServices
from ai_cat_controller.schemas.common import ApiResponse
from ai_cat_controller.schemas.dialog import DialogActionData, DialogRequest

router = APIRouter(prefix="/dialog", tags=["对话"])


@router.post(
    "/wake",
    response_model=ApiResponse[DialogActionData],
    summary="唤醒对话",
    description="请求适配器进入对话状态；第一阶段仅 Mock 适配器执行。",
)
async def wake_dialog(
    request: DialogRequest | None = None,
    services: AppServices = Depends(get_services),
) -> ApiResponse[DialogActionData]:
    result = await services.dialog.wake(request.request_id if request else None)
    return ApiResponse(
        message="对话已唤醒" if result["changed"] else "对话已经处于唤醒状态",
        data=DialogActionData.model_validate(result),
    )


@router.post(
    "/interrupt",
    response_model=ApiResponse[DialogActionData],
    summary="打断对话",
    description="请求适配器打断当前对话；第一阶段仅 Mock 适配器执行。",
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
