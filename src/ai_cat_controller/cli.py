"""Console entry point."""

import uvicorn

from ai_cat_controller.core.config import Settings
from ai_cat_controller.main import create_app


def main() -> None:
    settings = Settings.from_env()
    uvicorn.run(
        create_app(settings),
        host=settings.api_host,
        port=settings.api_port,
    )
