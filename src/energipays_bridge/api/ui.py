from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import pathlib

router = APIRouter()
_templates_dir = pathlib.Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_templates_dir))


_APP_VERSION = "1.1.5"


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    import time
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "cache_bust": int(time.time()),
            "root_path": request.headers.get("x-ingress-path", ""),
            "app_version": _APP_VERSION,
        },
    )
