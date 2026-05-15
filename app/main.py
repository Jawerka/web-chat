"""
Точка входа FastAPI-приложения web-chat.

Создаёт приложение и подключает роутеры.
На этапе 1 — только health; БД, MCP и статика подключаются на следующих этапах.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.router import api_router
from app.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Инициализация при старте и остановка при выключении."""
    settings.validate_timeouts()
    logger.info("web-chat запущен (PUBLIC_BASE_URL=%s)", settings.public_base_url)
    yield


def create_app() -> FastAPI:
    """Фабрика приложения (удобно для тестов)."""
    app = FastAPI(
        title="web-chat",
        description="LAN-чат с AI-агентом, MCP и Stable Diffusion",
        lifespan=lifespan,
    )
    app.include_router(api_router, prefix="/api")
    return app


app = create_app()
