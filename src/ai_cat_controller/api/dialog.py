"""Dialog control endpoints."""

from fastapi import APIRouter, Depends

from ai_cat_controller.api.dependencies import get_services
from ai_cat_controller.core.state import AppServices
from ai_cat_controller.schemas.common import ApiResponse
from ai_cat_controller.schemas.dialog import (
    DialogActionData,
    DialogConfigData,
    DialogConfigUpdate,
    DialogRequest,
    DialogStatusData,
    DialogSpeakRequest,
    DialogTextRequest,
)

router = APIRouter(prefix="/dialog", tags=["对话"])


@router.get(
    "/config",
    response_model=ApiResponse[DialogConfigData],
    summary="获取对话运行配置",
)
async def dialog_config(
    services: AppServices = Depends(get_services),
) -> ApiResponse[DialogConfigData]:
    result = await services.dialog.get_config()
    return ApiResponse(
        message="对话配置获取成功",
        data=DialogConfigData.model_validate(result),
    )


@router.patch(
    "/config",
    response_model=ApiResponse[DialogConfigData],
    summary="更新对话运行配置",
)
async def update_dialog_config(
    request: DialogConfigUpdate,
    services: AppServices = Depends(get_services),
) -> ApiResponse[DialogConfigData]:
    result = await services.dialog.update_config(request.follow_up_seconds)
    return ApiResponse(
        message="追问时间已保存，将在下一次追问窗口生效",
        data=DialogConfigData.model_validate(result),
    )


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


@router.post(
    "/text",
    response_model=ApiResponse[DialogActionData],
    summary="通过文字向真机提问",
    description="将文字问题送入当前实时会话，回答仍由真机扬声器播放。",
)
async def send_text_dialog(
    request: DialogTextRequest,
    services: AppServices = Depends(get_services),
) -> ApiResponse[DialogActionData]:
    result = await services.dialog.send_text(request.content, request.request_id)
    return ApiResponse(
        message="文字问题已发送，等待 AI 回答",
        data=DialogActionData.model_validate(result),
    )


@router.post(
    "/speak",
    response_model=ApiResponse[DialogActionData],
    summary="让真机主动播报固定文本",
    description=(
        "将短文本直接送入火山 TTS，不经过大模型。仅允许在无连续会话时调用，"
        "云端侧使用低优先级，发生竞争时不会打断用户。"
    ),
)
async def speak_text(
    request: DialogSpeakRequest,
    services: AppServices = Depends(get_services),
) -> ApiResponse[DialogActionData]:
    result = await services.dialog.speak(request.content, request.request_id)
    return ApiResponse(
        message="主动播报已发送",
        data=DialogActionData.model_validate(result),
    )
