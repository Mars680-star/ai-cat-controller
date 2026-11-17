"""Typed application state shared by request dependencies."""

from dataclasses import dataclass

from ai_cat_controller.adapters.base import AiCatAdapter
from ai_cat_controller.core.config import Settings
from ai_cat_controller.core.operation_gate import DeviceOperationGate
from ai_cat_controller.services.device_service import DeviceService
from ai_cat_controller.services.dialog_service import DialogService
from ai_cat_controller.services.mijia_gateway_service import MijiaGatewayService
from ai_cat_controller.services.motion_service import MotionService
from ai_cat_controller.services.personality_service import PersonalityService
from ai_cat_controller.services.product_mock_service import ProductMockService
from ai_cat_controller.persistence.sqlite_repository import SQLiteRepository
from ai_cat_controller.local_gateway.delegation import DelegationStore


@dataclass(slots=True)
class AppServices:
    settings: Settings
    adapter: AiCatAdapter
    device: DeviceService
    motion: MotionService
    dialog: DialogService
    personality: PersonalityService
    product: ProductMockService
    mijia: MijiaGatewayService
    maintenance_gate: DeviceOperationGate | None = None
    repository: SQLiteRepository | None = None
    local_gateway_delegations: DelegationStore | None = None
