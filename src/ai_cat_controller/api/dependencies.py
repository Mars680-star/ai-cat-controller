"""Typed request dependencies."""

from fastapi import Request

from ai_cat_controller.core.state import AppServices


def get_services(request: Request) -> AppServices:
    return request.app.state.services
