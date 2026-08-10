"""Product-experience Mock endpoints for the future mini program."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Path, Query, status

from ai_cat_controller.api.dependencies import get_services
from ai_cat_controller.core.state import AppServices
from ai_cat_controller.schemas.common import ApiResponse
from ai_cat_controller.schemas.product import (
    BindPetRequest,
    ExecuteActionRequest,
    FeedbackRequest,
    InteractionRequest,
    MockDeviceStatusRequest,
    MockLoginRequest,
    PetSettingsRequest,
    ProductDataResetRequest,
    SendDialogRequest,
)

router = APIRouter(tags=["产品 Mock"])


def _response(message: str, data: Any) -> ApiResponse[Any]:
    return ApiResponse(message=message, data=data)


async def mock_user_id(
    services: Annotated[AppServices, Depends(get_services)],
    session_token: Annotated[str | None, Header(alias="X-Mock-Session")] = None,
) -> str:
    return services.product.resolve_session(session_token)


@router.post("/auth/mock-login", response_model=ApiResponse[dict[str, Any]])
async def mock_login(
    request: MockLoginRequest,
    services: Annotated[AppServices, Depends(get_services)],
) -> ApiResponse[Any]:
    result = await services.product.login(request.login_code, request.nickname)
    return _response("Mock 登录成功", result)


@router.get("/personalities", response_model=ApiResponse[list[dict[str, Any]]])
async def personalities(
    services: Annotated[AppServices, Depends(get_services)],
) -> ApiResponse[Any]:
    return _response("性格配置列表", services.product.list_personalities())


@router.get("/pets", response_model=ApiResponse[list[dict[str, Any]]])
async def pets(
    services: Annotated[AppServices, Depends(get_services)],
    user_id: Annotated[str, Depends(mock_user_id)],
) -> ApiResponse[Any]:
    return _response("宠物列表", await services.product.list_pets(user_id))


@router.post("/pets/bind", response_model=ApiResponse[dict[str, Any]])
async def bind_pet(
    request: BindPetRequest,
    services: Annotated[AppServices, Depends(get_services)],
    user_id: Annotated[str, Depends(mock_user_id)],
) -> ApiResponse[Any]:
    result = await services.product.bind_pet(
        user_id=user_id,
        serial_number=request.device_serial,
        pet_name=request.pet_name,
        network_name=request.network_name,
    )
    return _response(
        "性格盲盒已揭晓" if result["blind_box_revealed"] else "设备已重新绑定",
        result,
    )


@router.get(
    "/pets/{pet_id}/dashboard",
    response_model=ApiResponse[dict[str, Any]],
)
async def pet_dashboard(
    pet_id: str,
    services: Annotated[AppServices, Depends(get_services)],
    user_id: Annotated[str, Depends(mock_user_id)],
) -> ApiResponse[Any]:
    return _response("宠物主页", await services.product.dashboard(user_id, pet_id))


@router.get(
    "/pets/{pet_id}/intimacy",
    response_model=ApiResponse[dict[str, Any]],
)
async def intimacy(
    pet_id: str,
    services: Annotated[AppServices, Depends(get_services)],
    user_id: Annotated[str, Depends(mock_user_id)],
) -> ApiResponse[Any]:
    return _response("亲密度详情", await services.product.intimacy(user_id, pet_id))


@router.post(
    "/pets/{pet_id}/interactions",
    response_model=ApiResponse[dict[str, Any]],
)
async def add_interaction(
    pet_id: str,
    request: InteractionRequest,
    services: Annotated[AppServices, Depends(get_services)],
    user_id: Annotated[str, Depends(mock_user_id)],
) -> ApiResponse[Any]:
    result = await services.product.add_interaction(
        user_id=user_id,
        pet_id=pet_id,
        event_type=request.event_type,
        request_id=request.request_id,
        metadata=request.metadata,
    )
    return _response("亲密度事件已记录", result)


@router.get(
    "/pets/{pet_id}/actions",
    response_model=ApiResponse[list[dict[str, Any]]],
)
async def action_catalog(
    pet_id: str,
    services: Annotated[AppServices, Depends(get_services)],
    user_id: Annotated[str, Depends(mock_user_id)],
) -> ApiResponse[Any]:
    return _response("安全预设动作库", await services.product.action_catalog(user_id, pet_id))


@router.post(
    "/pets/{pet_id}/actions/{action_id}/execute",
    response_model=ApiResponse[dict[str, Any]],
    status_code=status.HTTP_202_ACCEPTED,
)
async def execute_action(
    pet_id: str,
    action_id: str,
    request: ExecuteActionRequest,
    services: Annotated[AppServices, Depends(get_services)],
    user_id: Annotated[str, Depends(mock_user_id)],
) -> ApiResponse[Any]:
    result = await services.product.execute_action(
        user_id=user_id,
        pet_id=pet_id,
        action_id=action_id,
        request_id=request.request_id,
    )
    return _response("动作请求已处理", result)


@router.get(
    "/pets/{pet_id}/actions/executions",
    response_model=ApiResponse[list[dict[str, Any]]],
)
async def action_executions(
    pet_id: str,
    services: Annotated[AppServices, Depends(get_services)],
    user_id: Annotated[str, Depends(mock_user_id)],
) -> ApiResponse[Any]:
    return _response(
        "动作执行记录",
        await services.product.action_executions(user_id, pet_id),
    )


@router.post(
    "/pets/{pet_id}/dialogs",
    response_model=ApiResponse[dict[str, Any]],
)
async def send_dialog(
    pet_id: str,
    request: SendDialogRequest,
    services: Annotated[AppServices, Depends(get_services)],
    user_id: Annotated[str, Depends(mock_user_id)],
) -> ApiResponse[Any]:
    result = await services.product.send_dialog(
        user_id=user_id,
        pet_id=pet_id,
        content=request.content,
        trigger_action=request.trigger_action,
    )
    return _response("Mock 对话已生成", result)


@router.get(
    "/pets/{pet_id}/dialogs",
    response_model=ApiResponse[list[dict[str, Any]]],
)
async def dialog_history(
    pet_id: str,
    services: Annotated[AppServices, Depends(get_services)],
    user_id: Annotated[str, Depends(mock_user_id)],
    limit: Annotated[int, Query(ge=1, le=50)] = 50,
) -> ApiResponse[Any]:
    history = await services.product.dialog_history(user_id, pet_id)
    return _response("对话历史", history[:limit])


@router.get(
    "/pets/{pet_id}/dialog-conversations",
    response_model=ApiResponse[dict[str, Any]],
)
async def dialog_conversations(
    pet_id: str,
    services: Annotated[AppServices, Depends(get_services)],
    user_id: Annotated[str, Depends(mock_user_id)],
    limit: Annotated[int, Query(ge=1, le=50)] = 30,
) -> ApiResponse[Any]:
    result = await services.product.dialog_conversations(
        user_id,
        pet_id,
        limit,
    )
    return _response("对话会话已同步", result)


@router.get(
    "/pets/{pet_id}/dialog-conversations/{conversation_id}",
    response_model=ApiResponse[dict[str, Any]],
)
async def dialog_conversation_detail(
    pet_id: str,
    conversation_id: Annotated[str, Path(min_length=4, max_length=256)],
    services: Annotated[AppServices, Depends(get_services)],
    user_id: Annotated[str, Depends(mock_user_id)],
) -> ApiResponse[Any]:
    result = await services.product.dialog_conversation(
        user_id,
        pet_id,
        conversation_id,
    )
    return _response("会话详情已同步", result)


@router.patch(
    "/pets/{pet_id}/settings",
    response_model=ApiResponse[dict[str, Any]],
)
async def update_settings(
    pet_id: str,
    request: PetSettingsRequest,
    services: Annotated[AppServices, Depends(get_services)],
    user_id: Annotated[str, Depends(mock_user_id)],
) -> ApiResponse[Any]:
    result = await services.product.update_settings(
        user_id=user_id,
        pet_id=pet_id,
        name=request.name,
        volume=request.volume,
    )
    return _response("宠物设置已保存", result)


@router.post("/pets/{pet_id}/unbind", response_model=ApiResponse[dict[str, bool]])
async def unbind_pet(
    pet_id: str,
    services: Annotated[AppServices, Depends(get_services)],
    user_id: Annotated[str, Depends(mock_user_id)],
) -> ApiResponse[Any]:
    await services.product.unbind(user_id, pet_id)
    return _response("设备已解绑，性格与成长数据仍保留在设备实例", {"unbound": True})


@router.post(
    "/pets/{pet_id}/feedback",
    response_model=ApiResponse[dict[str, Any]],
)
async def feedback(
    pet_id: str,
    request: FeedbackRequest,
    services: Annotated[AppServices, Depends(get_services)],
    user_id: Annotated[str, Depends(mock_user_id)],
) -> ApiResponse[Any]:
    result = await services.product.feedback(
        user_id=user_id,
        pet_id=pet_id,
        category=request.category,
        content=request.content,
    )
    return _response("异常反馈已记录", result)


@router.post(
    "/pets/{pet_id}/mock-device-status",
    response_model=ApiResponse[dict[str, Any]],
)
async def update_mock_device_status(
    pet_id: str,
    request: MockDeviceStatusRequest,
    services: Annotated[AppServices, Depends(get_services)],
    user_id: Annotated[str, Depends(mock_user_id)],
) -> ApiResponse[Any]:
    result = await services.product.update_mock_device_status(
        user_id=user_id,
        pet_id=pet_id,
        online=request.online,
        battery_percent=request.battery_percent,
        charging=request.charging,
        network_status=request.network_status,
    )
    return _response("Mock 设备状态已更新", result)


@router.post(
    "/admin/reset-product-data",
    response_model=ApiResponse[dict[str, Any]],
)
async def reset_product_data(
    request: ProductDataResetRequest,
    services: Annotated[AppServices, Depends(get_services)],
    user_id: Annotated[str, Depends(mock_user_id)],
) -> ApiResponse[Any]:
    del request
    result = await services.product.reset_product_data(user_id=user_id)
    return _response("体验数据已格式化，请重新登录", result)
