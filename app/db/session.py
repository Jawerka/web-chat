"""
Асинхронная сессия SQLAlchemy и инициализация БД.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings
from app.db.sqlite import configure_sqlite_engine
from app.db.migrate import run_sqlite_migrations
from app.db.models import Base, Preset
from app.db.seed import PRESET_SEEDS
from app.integrations.media_utils import ensure_media_directories

logger = logging.getLogger(__name__)

engine = None
async_session_factory = None


def configure_database(database_url: str | None = None) -> None:
    """
    Создать или пересоздать engine и фабрику сессий.

    Используется при старте и в тестах (временная SQLite).
    """
    global engine, async_session_factory
    url = database_url or settings.database_url
    if engine is not None:
        engine.sync_engine.dispose()
    connect_args: dict = {}
    if "sqlite" in url:
        connect_args["timeout"] = 60.0
    engine = create_async_engine(url, echo=False, connect_args=connect_args)
    configure_sqlite_engine(engine)
    async_session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


configure_database()


def _ensure_db_directory() -> None:
    """Создать каталог для SQLite, если его ещё нет."""
    url = settings.database_url
    if "sqlite" not in url:
        return
    # sqlite+aiosqlite:///./data/db/web_chat.sqlite
    path_part = url.split("///", 1)[-1]
    if path_part.startswith("./"):
        db_path = Path(path_part[2:])
    else:
        db_path = Path(path_part)
    db_path.parent.mkdir(parents=True, exist_ok=True)


async def init_db() -> None:
    """Создать таблицы и заполнить пресеты при первом запуске."""
    _ensure_db_directory()
    ensure_media_directories()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await run_sqlite_migrations(engine)
    await _seed_presets_if_empty()


async def _seed_presets_if_empty() -> None:
    """Добавить три пресета из раздела 16, если таблица пуста."""
    async with async_session_factory() as session:
        result = await session.execute(select(Preset.id).limit(1))
        if result.scalar_one_or_none() is not None:
            return
        for seed in PRESET_SEEDS:
            session.add(
                Preset(
                    name=seed.name,
                    slug=seed.slug,
                    system_prompt=seed.system_prompt,
                    is_default=seed.is_default,
                    sort_order=seed.sort_order,
                )
            )
        await session.commit()
        logger.info("Загружены seed-пресеты (%d шт.)", len(PRESET_SEEDS))


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency: сессия БД на один запрос."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
