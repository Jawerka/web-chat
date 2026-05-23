"""
Запуск Alembic upgrade из приложения (Postgres и опционально SQLite).
"""

from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.db.url import alembic_database_url, database_url_raw

logger = logging.getLogger(__name__)

_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


def alembic_config(database_url: str | None = None) -> Config:
    """Конфиг Alembic с URL из .env."""
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", alembic_database_url(database_url))
    return cfg


def run_alembic_upgrade(revision: str = "head", *, database_url: str | None = None) -> None:
    """Синхронный ``alembic upgrade`` (вызывать из asyncio.to_thread при старте)."""
    url = database_url_raw(database_url)
    logger.info("Alembic upgrade %s → %s", revision, url.split("@")[-1] if "@" in url else url)
    command.upgrade(alembic_config(database_url), revision)


def run_alembic_stamp(revision: str, *, database_url: str | None = None) -> None:
    """Пометить БД ревизией без применения (миграция с существующей SQLite)."""
    command.stamp(alembic_config(database_url), revision)
