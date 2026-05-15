"""HTML-страницы (Jinja2)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

_ROOT = Path(__file__).resolve().parents[2]
templates = Jinja2Templates(directory=str(_ROOT / "templates"))

router = APIRouter(tags=["pages"])


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def chat_page(request: Request) -> HTMLResponse:
    """Главная страница чата."""
    return templates.TemplateResponse(
        request,
        "chat.html",
        {"title": "web-chat"},
    )
