from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ai_cat_controller.core.config import Settings
from ai_cat_controller.main import create_app


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        hardware_driver="mock",
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
