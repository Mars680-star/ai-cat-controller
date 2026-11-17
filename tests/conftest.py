import asyncio
from collections.abc import Iterator

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ai_cat_controller.core.config import Settings
from ai_cat_controller.main import create_app


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        hardware_driver="mock",
        enable_growth_personality_v1=True,
        motion_cooldown_seconds=0.0,
        service_status_cache_seconds=0.0,
        data_path=tmp_path / "ai-cat-test.db",
        dialog_config_path=tmp_path / "dialog-runtime-config.json",
    )


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def product_app_transport(client: TestClient):
    """Bridge requests on the actual TestClient lifespan loop, never a new one.

    The mini gateway has its own asyncio loop in these tests. A product app
    already started by TestClient owns asyncio locks/tasks on its portal loop;
    directly running that same app with ASGITransport on the pytest loop is not
    equivalent to an HTTP hop. Keep the strict production loop guard enabled.
    """
    transport = httpx.ASGITransport(app=client.app)

    class ProductAppLoopTransport:
        owner_loop_checks = 0

        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            assert client.portal is not None, "the original product lifespan must remain active"

            async def dispatch():
                gate = client.app.state.services.maintenance_gate
                assert gate is not None and gate._loop is asyncio.get_running_loop()
                self.owner_loop_checks += 1
                response = await transport.handle_async_request(request)
                try:
                    body = await response.aread()
                    # No live ASGI stream or loop-owned resource crosses back
                    # to the gateway loop; status/headers/body stay unchanged.
                    return httpx.Response(response.status_code, headers=response.headers,
                        content=body, request=request, extensions=response.extensions)
                finally:
                    await response.aclose()

            return await asyncio.to_thread(client.portal.call, dispatch)

    return ProductAppLoopTransport()
