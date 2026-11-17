"""FastAPI application entry point and dependency wiring.

Maintenance notes:
- Keep this module limited to startup/shutdown and service construction.
- Put device, dialog, motion, and growth behavior in their service modules.
- Any new long-running worker must be started and stopped in ``lifespan``.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from ai_cat_controller import __version__
from ai_cat_controller.adapters.factory import create_adapter
from ai_cat_controller.api.error_handlers import register_error_handlers
from ai_cat_controller.api.health import router as health_router
from ai_cat_controller.api.local_gateway import router as local_gateway_router
from ai_cat_controller.api.router import router as api_router
from ai_cat_controller.core.config import Settings
from ai_cat_controller.core.logging import configure_logging
from ai_cat_controller.core.operation_gate import DeviceOperationGate
from ai_cat_controller.core.process_gate import ProcessOperationGate
from ai_cat_controller.core.state import AppServices
from ai_cat_controller.persistence.sqlite_repository import SQLiteRepository
from ai_cat_controller.local_gateway.delegation import DelegationStore
from ai_cat_controller.services.device_service import DeviceService
from ai_cat_controller.services.dialog_service import DialogService
from ai_cat_controller.services.mijia_gateway_service import MijiaGatewayService
from ai_cat_controller.services.motion_service import MotionService
from ai_cat_controller.services.autonomy_service import InProcessAutonomyService
from ai_cat_controller.services.personality_service import PersonalityService
from ai_cat_controller.services.product_mock_service import ProductMockService
from ai_cat_controller.services.touch_event_monitor import TouchEventMonitor
from ai_cat_controller.web.router import router as web_router

LOGGER = logging.getLogger(__name__)
PACKAGE_DIR = Path(__file__).resolve().parent


async def _cleanup(name: str, operation: Callable[[], Awaitable[None]]) -> None:
    try:
        await operation()
    except Exception:
        LOGGER.exception("cleanup failed: %s", name)


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or Settings.from_env()

    @asynccontextmanager
    async def controller_lifespan(application: FastAPI, process_gate) -> AsyncIterator[None]:
        configure_logging(resolved_settings.log_level)
        maintenance_gate = DeviceOperationGate(process_gate=process_gate)
        application.state.maintenance_gate = maintenance_gate
        adapter = create_adapter(resolved_settings, operation_gate=maintenance_gate)
        await adapter.connect()
        motion = MotionService(
            adapter,
            command_timeout_seconds=resolved_settings.command_timeout_seconds,
            cooldown_seconds=resolved_settings.motion_cooldown_seconds,
            enable_motion=resolved_settings.enable_motion,
            has_tail=resolved_settings.has_tail,
            maintenance_gate=maintenance_gate,
        )
        dialog = DialogService(adapter, resolved_settings.dialog_config_path, maintenance_gate=maintenance_gate)
        device = DeviceService(adapter, resolved_settings, motion, dialog, maintenance_gate=maintenance_gate)
        personality = PersonalityService(resolved_settings)
        repository = SQLiteRepository(resolved_settings.data_path)
        product = ProductMockService(
            repository,
            motion,
            resolved_settings,
            personality,
            adapter,
            maintenance_gate=maintenance_gate,
        )
        await product.initialize()
        mijia = MijiaGatewayService(repository, product, device, dialog, motion, maintenance_gate=maintenance_gate)
        touch_monitor: TouchEventMonitor | None = None
        if resolved_settings.hardware_driver == "local_k1":
            touch_monitor = TouchEventMonitor(resolved_settings, product)
            await touch_monitor.start()
        application.state.services = AppServices(
            settings=resolved_settings,
            adapter=adapter,
            device=device,
            motion=motion,
            dialog=dialog,
            personality=personality,
            product=product,
            mijia=mijia,
            maintenance_gate=maintenance_gate,
            repository=repository,
            local_gateway_delegations=(DelegationStore(resolved_settings.local_gateway_delegation_path)
                if resolved_settings.enable_local_gateway_delegation else None),
        )
        autonomy_service: InProcessAutonomyService | None = None
        if (
            resolved_settings.hardware_driver == "local_k1"
            and resolved_settings.autonomy_in_process_enabled
        ):
            autonomy_service = InProcessAutonomyService.from_settings(
                resolved_settings
            )
            autonomy_service.start()
        LOGGER.info("AI Fox Controller started in %s mode", resolved_settings.hardware_driver)
        try:
            yield
        finally:
            if autonomy_service is not None:
                await _cleanup("in-process autonomy service", autonomy_service.close)
            if touch_monitor is not None:
                await _cleanup("touch event monitor", touch_monitor.close)
            await _cleanup("dialog service", dialog.shutdown)
            await _cleanup("motion service", motion.shutdown)
            await _cleanup("product mock service", product.shutdown)
            await _cleanup("device operation gate", maintenance_gate.shutdown)
            await _cleanup("adapter", adapter.close)
            LOGGER.info("AI Fox Controller stopped")

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        # Opening never provisions or repairs persistent coordination state.
        process_gate = (ProcessOperationGate(resolved_settings.process_gate_path)
            if resolved_settings.process_gate_path is not None else None)
        try:
            async with controller_lifespan(application, process_gate):
                yield
        finally:
            if process_gate is not None:
                process_gate.close()

    application = FastAPI(
        title="AI 狐狸控制器",
        description="SpaceMIT K1 AI 狐狸的安全控制 API。",
        version=__version__,
        lifespan=lifespan,
    )
    register_error_handlers(application)

    @application.middleware("http")
    async def operation_ingress(request, call_next):
        gate = getattr(application.state, "maintenance_gate", None)
        if gate is None:
            return await call_next(request)
        # Capture before dependencies, authentication or streamed body parsing.
        # Pure reads do not acquire a lease. Safety stop/interrupt bypass writes.
        with gate.request_scope():
            return await call_next(request)
    application.include_router(health_router)
    application.include_router(api_router)
    application.include_router(local_gateway_router)
    application.include_router(web_router)
    application.mount(
        "/static",
        StaticFiles(directory=PACKAGE_DIR / "static"),
        name="static",
    )
    return application


app = create_app()
