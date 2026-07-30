"""API router assembly."""

from fastapi import APIRouter, Depends

from ai_cat_controller.api import device, dialog, motion, product, services
from ai_cat_controller.core.security import require_api_key

router = APIRouter(
    prefix="/api/v1",
    dependencies=[Depends(require_api_key)],
)
router.include_router(device.router)
router.include_router(services.router)
router.include_router(motion.router)
router.include_router(dialog.router)
router.include_router(product.router)
