"""Uniform JSON error responses."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from ai_cat_controller.core.errors import AiCatError

LOGGER = logging.getLogger(__name__)


def _error_body(message: str, code: str, details: object) -> dict[str, object]:
    return {
        "success": False,
        "message": message,
        "data": {
            "code": code,
            "details": jsonable_encoder(details),
        },
    }


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AiCatError)
    async def handle_domain_error(
        request: Request, exc: AiCatError
    ) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(exc.message, exc.code, exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=422,
            content=_error_body("请求参数无效", "validation_error", exc.errors()),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        LOGGER.exception("unhandled request error: %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content=_error_body("服务内部错误", "internal_error", {}),
        )
