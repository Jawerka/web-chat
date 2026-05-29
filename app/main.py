"""
Точка входа FastAPI-приложения web-chat.

Создаёт приложение и подключает роутеры.
БД, загрузка, MCP+SD, WebSocket и веб-UI.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from app.api.gallery import router as gallery_router
from app.api.media import router as media_router
from app.api.pages import router as pages_router
from app.api.router import api_router
from app.api.websocket import router as ws_router
from app.config import settings
from app.db.session import init_db
from app.integrations.mcp_server import start_mcp_background
from app.logging_buffer import ensure_log_buffer_attached, set_main_event_loop
from app.logging_setup import setup_logging
from app.middleware.access_control import AccessControlMiddleware
from app.middleware.session_auth import SessionAuthMiddleware
from app.middleware.public_base_url import PublicBaseUrlMiddleware
from app.public_url import public_base_url_lan, public_base_url_vpn
from app.services.job_queue import heavy_job_queue
from app.services.retention_task import start_retention_background
from app.security.trusted_internal import refresh_trusted_internal_from_settings
from app.services.shutdown_service import graceful_shutdown

_ROOT = Path(__file__).resolve().parents[1]

setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Инициализация при старте и остановка при выключении."""
    set_main_event_loop(asyncio.get_running_loop())
    ensure_log_buffer_attached()
    refresh_trusted_internal_from_settings()
    settings.validate_timeouts()
    await init_db()
    from app.api.ws_events import emit_progress
    from app.api.ws_manager import manager
    from app.services.turn_realtime import configure_turn_realtime

    configure_turn_realtime(manager, progress_emit=emit_progress)
    await heavy_job_queue.start()
    start_mcp_background()
    retention_task, retention_stop = start_retention_background()
    vpn = public_base_url_vpn()
    logger.info(
        "web-chat запущен (PUBLIC_BASE_URL=%s%s, MCP :%d)",
        public_base_url_lan(),
        f", VPN={vpn}" if vpn else "",
        settings.effective_mcp_port,
    )
    yield
    await graceful_shutdown()
    retention_stop.set()
    if isinstance(retention_task, asyncio.Task):
        retention_task.cancel()
        try:
            await retention_task
        except asyncio.CancelledError:
            pass


def create_app() -> FastAPI:
    """Фабрика приложения (удобно для тестов)."""
    app = FastAPI(
        title="web-chat",
        description="LAN-чат с AI-агентом, MCP и Stable Diffusion",
        lifespan=lifespan,
    )
    app.add_middleware(PublicBaseUrlMiddleware)
    app.add_middleware(SessionAuthMiddleware)
    app.add_middleware(AccessControlMiddleware)
    app.include_router(pages_router)
    app.include_router(gallery_router)
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
            "</svg>"
        )
        return Response(content=svg, media_type="image/svg+xml")

    return app


app = create_app()
