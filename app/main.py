"""
Точка входа FastAPI-приложения web-chat.

Создаёт приложение и подключает роутеры.
БД, загрузка, MCP+SD, WebSocket и веб-UI.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from app.api.media import router as media_router
from app.api.pages import router as pages_router
from app.api.router import api_router
from app.api.websocket import router as ws_router
from app.config import settings
from app.db.session import init_db
from app.logging_buffer import install_log_buffer
from app.integrations.mcp_server import start_mcp_background

_ROOT = Path(__file__).resolve().parents[1]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Инициализация при старте и остановка при выключении."""
    settings.validate_timeouts()
    install_log_buffer()
    await init_db()
    start_mcp_background()
    logger.info(
        "web-chat запущен (PUBLIC_BASE_URL=%s, MCP :%d)",
        settings.public_base_url,
        settings.effective_mcp_port,
    )
    yield


def create_app() -> FastAPI:
    """Фабрика приложения (удобно для тестов)."""
    app = FastAPI(
        title="web-chat",
        description="LAN-чат с AI-агентом, MCP и Stable Diffusion",
        lifespan=lifespan,
    )
    app.include_router(pages_router)
    app.include_router(api_router, prefix="/api")
    app.include_router(media_router)
    app.include_router(ws_router)
    app.mount("/static", StaticFiles(directory=str(_ROOT / "static")), name="static")

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> Response:
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
            '<rect width="32" height="32" rx="6" fill="#1a2332"/>'
            '<path d="M8 10h16v12H8z" fill="none" stroke="#5b9fd4" stroke-width="2"/>'
            '</svg>'
        )
        return Response(content=svg, media_type="image/svg+xml")

    return app


app = create_app()
