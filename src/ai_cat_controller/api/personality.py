"""Active device personality synchronization status."""

from typing import Any

from fastapi import APIRouter, Depends

from ai_cat_controller.api.dependencies import get_services
from ai_cat_controller.core.state import AppServices
from ai_cat_controller.schemas.common import ApiResponse

router = APIRouter(prefix="/personality", tags=["性格同步"])


@router.get(
    "/runtime",
    response_model=ApiResponse[dict[str, Any]],
    summary="获取当前设备的真实运行时性格",
)
async def personality_runtime(
    services: AppServices = Depends(get_services),
) -> ApiResponse[dict[str, Any]]:
    profile = await services.personality.status()
    dialog_status = await services.dialog.get_status()
    applied_revision = dialog_status.get("personality_revision")
    configured_revision = profile.get("revision")
    profile["native_applied"] = bool(
        configured_revision and configured_revision == applied_revision
    )
    profile["native_revision"] = applied_revision
    return ApiResponse(
        message="运行时性格获取成功",
        data=profile,
    )
