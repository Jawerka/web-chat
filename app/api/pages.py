"""HTML-страницы (Jinja2)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

_ROOT = Path(__file__).resolve().parents[2]
templates = Jinja2Templates(directory=str(_ROOT / "templates"))

router = APIRouter(tags=["pages"])


@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_page(request: Request) -> HTMLResponse:
    """Страница входа."""
    return templates.TemplateResponse(
        request,
        "login.html",
        {"title": "Вход — web-chat"},
    )


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def chat_page(request: Request) -> HTMLResponse:
    """Главная страница чата."""
    return templates.TemplateResponse(
        request,
        "chat.html",
        {"title": "web-chat"},
    )


@router.get("/macros", response_class=HTMLResponse, include_in_schema=False)
async def macros_page(request: Request) -> HTMLResponse:
    """Страница быстрых промптов (@alias)."""
    return templates.TemplateResponse(
        request,
        "macros.html",
        {"title": "Быстрые промпты"},
    )


@router.get("/health", response_class=HTMLResponse, include_in_schema=False)
async def health_dashboard(request: Request) -> HTMLResponse:
    """Дашборд состояния сервисов."""
    return templates.TemplateResponse(
        request,
        "health.html",
        {"title": "Состояние сервисов"},
    )
