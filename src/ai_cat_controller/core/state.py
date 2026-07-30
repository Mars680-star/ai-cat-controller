"""Typed application state shared by request dependencies."""

from dataclasses import dataclass

from ai_cat_controller.adapters.base import AiCatAdapter
from ai_cat_controller.core.config import Settings
from ai_cat_controller.services.device_service import DeviceService
from ai_cat_controller.services.dialog_service import DialogService
from ai_cat_controller.services.motion_service import MotionService


@dataclass(slots=True)
class AppServices:
    settings: Settings
    adapter: AiCatAdapter
    device: DeviceService
    motion: MotionService
    dialog: DialogService
