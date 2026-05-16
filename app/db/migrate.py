"""
Лёгкие миграции SQLite при старте (без Alembic).
"""

from __future__ import annotations

import logging
import uuid

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

        await _migrate_preset_prompts(conn)
        await _normalize_dashed_uuid_ids(conn)


async def _normalize_dashed_uuid_ids(conn) -> None:
    """
    Исправить UUID с дефисами в TEXT-колонках SQLite.

    SQLAlchemy Uuid ищет по 32 hex-символам; сырой INSERT str(uuid4()) оставляет дефисы —
    get_by_id и PATCH preset_id тогда возвращают 404.
    """
    presets = await conn.execute(text("SELECT COUNT(*) FROM presets WHERE id LIKE '%-%'"))
    convs = await conn.execute(
        text("SELECT COUNT(*) FROM conversations WHERE preset_id LIKE '%-%'"),
    )
    if (presets.scalar() or 0) == 0 and (convs.scalar() or 0) == 0:
        return

    await conn.execute(text("PRAGMA foreign_keys=OFF"))
    await conn.execute(
        text(
            "UPDATE conversations SET preset_id = REPLACE(preset_id, '-', '') "
            "WHERE preset_id LIKE '%-%'"
        ),
    )
    await conn.execute(
        text("UPDATE presets SET id = REPLACE(id, '-', '') WHERE id LIKE '%-%'"),
    )
    await conn.execute(text("PRAGMA foreign_keys=ON"))
    logger.info("Миграция: нормализованы UUID пресетов (убраны дефисы)")


async def _migrate_preset_prompts(conn) -> None:
    """Обновить промпты image_gen и добавить пресет img2img (если БД уже была заполнена)."""
    from app.db.seed import IMAGE_GEN_PROMPT, IMG2IMG_PRESET_PROMPT

    count_row = await conn.execute(text("SELECT COUNT(*) FROM presets"))
    if (count_row.scalar() or 0) == 0:
        return

    result = await conn.execute(text("SELECT id FROM presets WHERE slug = 'image_gen' LIMIT 1"))
    if result.fetchone() is not None:
        await conn.execute(
            text("UPDATE presets SET system_prompt = :prompt, name = :name WHERE slug = 'image_gen'"),
            {
                "prompt": IMAGE_GEN_PROMPT,
                "name": "Генерация с нуля (txt2img)",
            },
        )
        logger.info("Миграция: presets.image_gen")

    result = await conn.execute(text("SELECT id FROM presets WHERE slug = 'img2img' LIMIT 1"))
    if result.fetchone() is None:
        preset_id = uuid.uuid4().hex
        await conn.execute(
            text(
                "INSERT INTO presets (id, name, slug, system_prompt, is_default, sort_order) "
                "VALUES (:id, :name, 'img2img', :prompt, 0, 2)"
            ),
            {
                "id": preset_id,
                "name": "Перерисовка (img2img)",
                "prompt": IMG2IMG_PRESET_PROMPT,
            },
        )
        logger.info("Миграция: добавлен presets.img2img")
    else:
        await conn.execute(
            text(
                "UPDATE presets SET system_prompt = :prompt, name = :name, sort_order = 2 "
                "WHERE slug = 'img2img'"
            ),
            {
                "prompt": IMG2IMG_PRESET_PROMPT,
                "name": "Перерисовка (img2img)",
            },
        )
        logger.info("Миграция: presets.img2img system_prompt")

    await conn.execute(
        text("UPDATE presets SET sort_order = 3 WHERE slug = 'document_analysis'"),
    )
