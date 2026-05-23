"""
ETL: копирование данных web-chat из SQLite в PostgreSQL (сохранение UUID и FK).

Порядок таблиц учитывает внешние ключи. Подходит и для тестового SQLite→SQLite.
"""

from __future__ import annotations

import enum
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, TypeVar

from sqlalchemy import delete, func, insert, inspect, select, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.db.alembic_runner import run_alembic_stamp, run_alembic_upgrade
from app.db.migrate import run_sqlite_migrations
from app.db.models import (
    Attachment,
    Base,
    Conversation,
    MediaAsset,
    Message,
    Preset,
    PromptMacro,
)
from app.db.url import is_postgres_url, is_sqlite_url, normalize_async_database_url

logger = logging.getLogger(__name__)

ModelT = TypeVar("ModelT", bound=DeclarativeBase)

# Порядок загрузки (родители → дети)
ETL_TABLES: list[tuple[str, type[ModelT]]] = [
    ("presets", Preset),
    ("prompt_macros", PromptMacro),
    ("conversations", Conversation),
    ("media_assets", MediaAsset),
    ("messages", Message),
    ("attachments", Attachment),
]

# Обратный порядок для TRUNCATE
ETL_TABLES_REVERSE = list(reversed(ETL_TABLES))


@dataclass
class EtlCounts:
    """Счётчики по таблицам (источник / приёмник)."""

    source: dict[str, int] = field(default_factory=dict)
    target_before: dict[str, int] = field(default_factory=dict)
    copied: dict[str, int] = field(default_factory=dict)
    target_after: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": dict(self.source),
            "target_before": dict(self.target_before),
            "copied": dict(self.copied),
            "target_after": dict(self.target_after),
        }


@dataclass
class EtlOptions:
    source_url: str
    target_url: str
    dry_run: bool = False
    truncate_target: bool = False
    skip_media_assets: bool = False
    batch_size: int = 100
    stamp_alembic: bool = True


class EtlError(Exception):
    """Ошибка валидации или переноса."""


def validate_etl_urls(source_url: str, target_url: str) -> None:
    """Источник — SQLite; приёмник — Postgres (или SQLite только в тестах)."""
    if not is_sqlite_url(source_url):
        raise EtlError(f"Источник должен быть SQLite, получено: {source_url!r}")
    if not is_postgres_url(target_url) and not is_sqlite_url(target_url):
        raise EtlError(f"Неподдерживаемый URL приёмника: {target_url!r}")
    if _normalize_url_key(source_url) == _normalize_url_key(target_url):
        raise EtlError("Источник и приёмник совпадают")


def _normalize_url_key(url: str) -> str:
    return normalize_async_database_url(url).rstrip("/")


def create_etl_engine(url: str) -> AsyncEngine:
    norm = normalize_async_database_url(url)
    connect_args: dict[str, Any] = {}
    kwargs: dict[str, Any] = {"echo": False}
    if is_sqlite_url(norm):
        connect_args["timeout"] = 120.0
    elif is_postgres_url(norm):
        kwargs["pool_pre_ping"] = True
    kwargs["connect_args"] = connect_args
    return create_async_engine(norm, **kwargs)


async def count_table(session: AsyncSession, model: type[ModelT]) -> int:
    result = await session.execute(select(func.count()).select_from(model))
    return int(result.scalar() or 0)


async def count_all_tables(session: AsyncSession) -> dict[str, int]:
    return {name: await count_table(session, model) for name, model in ETL_TABLES}


async def _truncate_target(session: AsyncSession, target_url: str) -> None:
    if is_postgres_url(target_url):
        tables = ", ".join(name for name, _ in ETL_TABLES_REVERSE)
        await session.execute(
            text(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE"),
        )
    else:
        await session.execute(text("PRAGMA foreign_keys=OFF"))
        for _, model in ETL_TABLES_REVERSE:
            await session.execute(delete(model))
        await session.execute(text("PRAGMA foreign_keys=ON"))
    await session.flush()


def _row_dict(obj: ModelT) -> dict[str, Any]:
    """Словарь колонок ORM-объекта для INSERT."""
    mapper = inspect(type(obj))
    out: dict[str, Any] = {}
    for col in mapper.columns:
        val = getattr(obj, col.key)
        if isinstance(val, uuid.UUID):
            out[col.key] = val
        elif isinstance(val, enum.Enum):
            out[col.key] = val.value
        else:
            out[col.key] = val
    out = _normalize_sqlite_enum_strings(type(obj), out)
    return out


def _normalize_sqlite_enum_strings(model: type[ModelT], row: dict[str, Any]) -> dict[str, Any]:
    """
    SQLite хранит имена Enum (USER, CHARACTER); Postgres — значения (user, character).
    """
    mapper = inspect(model)
    for col in mapper.columns:
        key = col.key
        if key not in row:
            continue
        val = row[key]
        if not isinstance(val, str):
            continue
        enum_cls: type[enum.Enum] | None = None
        if isinstance(col.type, SAEnum):
            enum_cls = col.type.enum_class
        elif hasattr(col.type, "python_type"):
            pt = col.type.python_type
            if isinstance(pt, type) and issubclass(pt, enum.Enum):
                enum_cls = pt
        if enum_cls is None:
            continue
        values = {m.value for m in enum_cls}
        if val in values:
            continue
        if val in enum_cls.__members__:
            row[key] = enum_cls[val].value
        elif val.lower() in values:
            row[key] = val.lower()
    return row


async def _load_id_set(session: AsyncSession, model: type[ModelT]) -> set[uuid.UUID]:
    pk = inspect(model).primary_key[0]
    result = await session.execute(select(pk))
    return set(result.scalars().all())


def _sanitize_fk_row(
    model: type[ModelT],
    row: dict[str, Any],
    *,
    valid_conversation_ids: set[uuid.UUID] | None,
    valid_message_ids: set[uuid.UUID] | None,
    valid_media_asset_ids: set[uuid.UUID] | None,
) -> dict[str, Any]:
    """Обнулить битые FK (удалённые беседы/сообщения в legacy SQLite)."""
    out = dict(row)
    if valid_conversation_ids is not None and "conversation_id" in out:
        cid = out.get("conversation_id")
        if cid is not None and cid not in valid_conversation_ids:
            out["conversation_id"] = None
    if valid_message_ids is not None and "message_id" in out:
        mid = out.get("message_id")
        if mid is not None and mid not in valid_message_ids:
            out["message_id"] = None
    if valid_media_asset_ids is not None and "media_asset_id" in out:
        aid = out.get("media_asset_id")
        if aid is not None and aid not in valid_media_asset_ids:
            out["media_asset_id"] = None
    return out


async def _copy_table_clean(
    source: AsyncSession,
    target: AsyncSession,
    model: type[ModelT],
    *,
    batch_size: int,
    valid_conversation_ids: set[uuid.UUID] | None = None,
    valid_message_ids: set[uuid.UUID] | None = None,
    valid_media_asset_ids: set[uuid.UUID] | None = None,
    commit_each_batch: bool = False,
) -> int:
    """Копия батчами: читаем source, вставляем в target новые объекты."""
    total = 0
    offset = 0
    pk_col = inspect(model).primary_key[0]
    while True:
        result = await source.execute(
            select(model).order_by(pk_col).offset(offset).limit(batch_size),
        )
        batch = list(result.scalars().all())
        if not batch:
            break
        rows = [_row_dict(obj) for obj in batch]
        if valid_conversation_ids is not None and model is Message:
            rows = [
                row
                for row in rows
                if row.get("conversation_id") in valid_conversation_ids
            ]
        else:
            rows = [
                _sanitize_fk_row(
                    model,
                    row,
                    valid_conversation_ids=valid_conversation_ids,
                    valid_message_ids=valid_message_ids,
                    valid_media_asset_ids=valid_media_asset_ids,
                )
                for row in rows
            ]
        if not rows:
            offset += batch_size
            continue
        await target.execute(insert(model), rows)
        if commit_each_batch:
            await target.commit()
        else:
            await target.flush()
        for obj in batch:
            source.expunge(obj)
        total += len(batch)
        offset += batch_size
    return total


async def _ensure_target_schema(target_engine: AsyncEngine, target_url: str) -> None:
    """Пустой приёмник: таблицы для подсчёта и вставки."""
    if is_postgres_url(target_url):
        import asyncio

        await asyncio.to_thread(run_alembic_upgrade, "head", database_url=target_url)
    else:
        async with target_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)


async def run_etl(options: EtlOptions) -> EtlCounts:
    """
    Перенос данных source → target.

    При не-dry_run на Postgres: ``alembic upgrade head`` перед вставкой.
    """
    validate_etl_urls(options.source_url, options.target_url)

    source_engine = create_etl_engine(options.source_url)
    target_engine = create_etl_engine(options.target_url)
    SourceSession = async_sessionmaker(source_engine, expire_on_commit=False)
    TargetSession = async_sessionmaker(target_engine, expire_on_commit=False)

    stats = EtlCounts()

    try:
        if is_sqlite_url(options.source_url):
            await run_sqlite_migrations(source_engine)
        await _ensure_target_schema(target_engine, options.target_url)

        async with SourceSession() as src_sess:
            stats.source = await count_all_tables(src_sess)
            if options.skip_media_assets:
                stats.source["media_assets"] = 0

        async with TargetSession() as tgt_sess:
            stats.target_before = await count_all_tables(tgt_sess)

        if options.dry_run:
            logger.info("ETL dry-run: %s", stats.as_dict())
            return stats

        non_empty = sum(stats.target_before.values())
        if non_empty and not options.truncate_target:
            raise EtlError(
                "Приёмник не пуст. Укажите --truncate-target для полной перезаписи "
                f"(сейчас: {stats.target_before})",
            )

        async with SourceSession() as src_sess, TargetSession() as tgt_sess:
            if options.truncate_target:
                await _truncate_target(tgt_sess, options.target_url)
                await tgt_sess.commit()

            valid_conv_ids = await _load_id_set(src_sess, Conversation)
            valid_msg_ids: set[uuid.UUID] = set()
            valid_media_ids: set[uuid.UUID] = set()

            for table_name, model in ETL_TABLES:
                if options.skip_media_assets and model is MediaAsset:
                    logger.info("Пропуск таблицы %s (--skip-media)", table_name)
                    stats.copied[table_name] = 0
                    continue
                fk_conv = fk_msg = fk_media = None
                if model in (MediaAsset, Message):
                    fk_conv = valid_conv_ids
                elif model is Attachment:
                    fk_conv = valid_conv_ids
                    fk_msg = valid_msg_ids
                    fk_media = valid_media_ids
                media_batch = min(options.batch_size, 5) if model is MediaAsset else options.batch_size
                n = await _copy_table_clean(
                    src_sess,
                    tgt_sess,
                    model,
                    batch_size=media_batch,
                    valid_conversation_ids=fk_conv,
                    valid_message_ids=fk_msg,
                    valid_media_asset_ids=fk_media,
                    commit_each_batch=model is MediaAsset,
                )
                if model is Conversation:
                    valid_conv_ids = await _load_id_set(tgt_sess, Conversation)
                elif model is Message:
                    valid_msg_ids = await _load_id_set(tgt_sess, Message)
                elif model is MediaAsset:
                    valid_media_ids = await _load_id_set(tgt_sess, MediaAsset)
                stats.copied[table_name] = n
                logger.info("Скопировано %s: %d строк", table_name, n)
                if model is not MediaAsset:
                    await tgt_sess.commit()

        if options.stamp_alembic and is_postgres_url(options.target_url):
            import asyncio

            await asyncio.to_thread(
                run_alembic_stamp,
                "head",
                database_url=options.target_url,
            )

        async with TargetSession() as tgt_sess:
            stats.target_after = await count_all_tables(tgt_sess)

        _verify_counts(stats, options.skip_media_assets)
        logger.info("ETL завершён: %s", stats.as_dict())
        return stats

    finally:
        await source_engine.dispose()
        await target_engine.dispose()


def _verify_counts(stats: EtlCounts, skip_media: bool) -> None:
    """Сверка source vs target_after."""
    mismatches: list[str] = []
    for table_name, _ in ETL_TABLES:
        if skip_media and table_name == "media_assets":
            continue
        src_n = stats.source.get(table_name, 0)
        dst_n = stats.target_after.get(table_name, 0)
        if src_n != dst_n:
            mismatches.append(f"{table_name}: source={src_n} target={dst_n}")
    if mismatches:
        raise EtlError("Расхождение счётчиков после ETL: " + "; ".join(mismatches))
