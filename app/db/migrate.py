"""
Лёгкие миграции SQLite при старте (без Alembic).
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)


async def run_sqlite_migrations(engine: AsyncEngine) -> None:
    """Добавить новые колонки/таблицы, если их ещё нет."""
    if "sqlite" not in str(engine.url):
        return

    async with engine.begin() as conn:
        result = await conn.execute(text("PRAGMA table_info(attachments)"))
        cols = {row[1] for row in result.fetchall()}
        if "media_asset_id" not in cols:
            await conn.execute(
                text(
                    "ALTER TABLE attachments ADD COLUMN media_asset_id "
                    "TEXT REFERENCES media_assets(id)"
                )
            )
            logger.info("Миграция: attachments.media_asset_id")
