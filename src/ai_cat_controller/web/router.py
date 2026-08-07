"""HTML control page routes."""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

PACKAGE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")

router = APIRouter(include_in_schema=False)


@router.get("/", response_class=RedirectResponse)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/control", status_code=307)


@router.get("/control", response_class=HTMLResponse)
async def control_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="control.html",
        context={},
    )


@router.head("/control")
async def control_page_head() -> Response:
    return Response(media_type="text/html")
