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

        await _migrate_image_gen_preset_prompt(conn)


async def _migrate_image_gen_preset_prompt(conn) -> None:
    """Обновить system_prompt пресета генерации изображений (без ![...](url) в ответах)."""
    from app.db.seed import IMAGE_GEN_PROMPT

    result = await conn.execute(
        text("SELECT id FROM presets WHERE slug = 'image_gen' LIMIT 1")
    )
    if result.fetchone() is None:
        return
    await conn.execute(
        text("UPDATE presets SET system_prompt = :prompt WHERE slug = 'image_gen'"),
        {"prompt": IMAGE_GEN_PROMPT},
    )
    logger.info("Миграция: presets.image_gen system_prompt")
