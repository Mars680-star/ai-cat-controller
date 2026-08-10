"""FastAPI application entry point."""

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
from ai_cat_controller.api.router import router as api_router
from ai_cat_controller.core.config import Settings
from ai_cat_controller.core.logging import configure_logging
from ai_cat_controller.core.state import AppServices
from ai_cat_controller.persistence.sqlite_repository import SQLiteRepository
from ai_cat_controller.services.device_service import DeviceService
from ai_cat_controller.services.dialog_service import DialogService
from ai_cat_controller.services.motion_service import MotionService
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
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        configure_logging(resolved_settings.log_level)
        adapter = create_adapter(resolved_settings)
        await adapter.connect()
        motion = MotionService(
            adapter,
            command_timeout_seconds=resolved_settings.command_timeout_seconds,
            cooldown_seconds=resolved_settings.motion_cooldown_seconds,
        )
        dialog = DialogService(adapter, resolved_settings.dialog_config_path)
        device = DeviceService(adapter, resolved_settings, motion, dialog)
        personality = PersonalityService(resolved_settings)
        product = ProductMockService(
            SQLiteRepository(resolved_settings.data_path),
            motion,
            resolved_settings,
            personality,
            adapter,
        )
        await product.initialize()
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
        )
        LOGGER.info("AI Cat Controller started in %s mode", resolved_settings.hardware_driver)
        try:
            yield
        finally:
            if touch_monitor is not None:
                await _cleanup("touch event monitor", touch_monitor.close)
            await _cleanup("dialog service", dialog.shutdown)
            await _cleanup("motion service", motion.shutdown)
            await _cleanup("product mock service", product.shutdown)
            await _cleanup("adapter", adapter.close)
            LOGGER.info("AI Cat Controller stopped")

    application = FastAPI(
        title="AI 猫控制器",
        description="SpaceMIT K1 AI 猫的安全控制 API。",
        version=__version__,
        lifespan=lifespan,
    )
    register_error_handlers(application)
    application.include_router(health_router)
    application.include_router(api_router)
    application.include_router(web_router)
    application.mount(
        "/static",
        StaticFiles(directory=PACKAGE_DIR / "static"),
        name="static",
    )
    return application


app = create_app()
