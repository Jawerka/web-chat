"""
Финальная очистка тестовых бесед после pytest-сессии.

Вызывается только из pytest_sessionfinish — не во время тестов.

Безопасность (см. tests/safety.py, HANDBOOK.md §14.4):
- SQLite: только зарегистрированные tmp-файлы, никогда production DATABASE_URL;
- Live HTTP: только WEB_CHAT_TEST_BASE_URL (или PUBLIC + ALLOW_PUBLIC_CLEANUP);
- Live: только заголовки [pytest]; сироты — только при WEB_CHAT_TEST_CLEANUP_ORPHANS=1;
- id из tmp SQLite не удаляются на live-сервере.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from pathlib import Path

import httpx

from app.db.repositories import ConversationRepository
from app.db.session import configure_database, dispose_database, init_db
from tests.conventions import (
    TEST_CONVERSATION_PREFIX,
    is_likely_test_orphan_title,
    is_test_conversation_title,
)
from tests.safety import (
    is_production_database_url,
    is_safe_test_database_url,
    live_cleanup_explicitly_enabled,
    resolve_safe_live_cleanup_base_url,
)

logger = logging.getLogger(__name__)

# SQLite-файлы из фикстур (только безопасные, не production)
_recorded_database_urls: set[str] = set()

# ID бесед по URL tmp SQLite
_recorded_conversation_ids_by_db: dict[str, set[str]] = {}


def _normalize_db_url(url: str) -> str:
    if "///" in url:
        return url.split("///", 1)[-1]
    return url


def _current_db_key() -> str:
    from app.config import settings
    from app.db.session import engine

    if engine is not None:
        return _normalize_db_url(str(engine.url))
    return _normalize_db_url(settings.database_url)


def record_test_database_url(database_url: str) -> None:
    """Запомнить URL tmp SQLite (отклоняет production DATABASE_URL)."""
    if not database_url or "sqlite" not in database_url:
        return
    if not is_safe_test_database_url(database_url):
        logger.error(
            "Пропуск регистрации production БД для очистки: %s",
            database_url,
        )
        return
    _recorded_database_urls.add(database_url)


def clear_recorded_database_urls() -> None:
    _recorded_database_urls.clear()


def record_test_conversation_id(
    conversation_id: str | uuid.UUID,
    *,
    database_url: str | None = None,
) -> None:
    """Запомнить id беседы для очистки tmp SQLite (не для live HTTP)."""
    if database_url:
        if not is_safe_test_database_url(database_url):
            return
        key = _normalize_db_url(database_url)
    else:
        from app.db.session import engine

        if engine is None:
            return
        if is_production_database_url(str(engine.url)):
            return
        key = _normalize_db_url(str(engine.url))
    _recorded_conversation_ids_by_db.setdefault(key, set()).add(str(conversation_id))


def clear_recorded_conversation_ids() -> None:
    _recorded_conversation_ids_by_db.clear()


def should_cleanup_orphan_titles_on_live() -> bool:
    """
    Удалять сироты («Новая беседа», «t») на live.

    По умолчанию выключено — только WEB_CHAT_TEST_CLEANUP_ORPHANS=1
    и явный WEB_CHAT_TEST_BASE_URL.
    """
    if not live_cleanup_explicitly_enabled():
        return False
    val = (os.environ.get("WEB_CHAT_TEST_CLEANUP_ORPHANS") or "0").strip().lower()
    return val in ("1", "true", "yes", "on")


def run_session_cleanup() -> None:
    """Синхронная обёртка для pytest_sessionfinish."""
    if os.environ.get("WEB_CHAT_TEST_CLEANUP", "1").strip().lower() in (
        "0",
        "false",
        "no",
        "off",
    ):
        logger.debug("Очистка тестовых бесед отключена (WEB_CHAT_TEST_CLEANUP=0)")
        return
    try:
        asyncio.run(_session_cleanup_async())
    except Exception:
        logger.exception("Ошибка финальной очистки тестовых бесед")


async def _session_cleanup_async() -> None:
    live_deleted = await cleanup_live_test_conversations()
    db_deleted = await cleanup_recorded_test_databases()
    total = live_deleted + db_deleted
    if total:
        logger.info(
            "Финишер pytest: удалено тестовых бесед — live=%d, db=%d",
            live_deleted,
            db_deleted,
        )

    clear_recorded_conversation_ids()

    from app.config import settings

    await dispose_database()
    configure_database(settings.database_url)


async def delete_test_conversations_in_session(
    session,
    *,
    database_url: str | None = None,
) -> int:
    """Удалить беседы с префиксом [pytest] и id, зарегистрированные для этой tmp БД."""
    repo = ConversationRepository(session)
    seen: set[uuid.UUID] = set()
    to_delete = []

    for conv in await repo.list_with_title_prefix(TEST_CONVERSATION_PREFIX):
        if conv.id not in seen:
            seen.add(conv.id)
            to_delete.append(conv)

    db_key = _normalize_db_url(database_url) if database_url else _current_db_key()
    for raw_id in _recorded_conversation_ids_by_db.get(db_key, set()):
        try:
            conv_id = uuid.UUID(raw_id)
        except ValueError:
            continue
        if conv_id in seen:
            continue
        conv = await repo.get_by_id(conv_id)
        if conv is not None:
            seen.add(conv_id)
            to_delete.append(conv)

    for conv in to_delete:
        await repo.delete_permanent(conv)
    if to_delete:
        await session.commit()
    return len(to_delete)


async def cleanup_recorded_test_databases() -> int:
    """Пройти по зарегистрированным tmp SQLite и удалить тестовые беседы."""
    total = 0
    urls = sorted(_recorded_database_urls)
    clear_recorded_database_urls()

    for url in urls:
        if not is_safe_test_database_url(url):
            logger.warning("Пропуск очистки небезопасной БД: %s", url)
            continue
        path = _sqlite_path_from_url(url)
        if path is not None and not path.is_file():
            continue
        try:
            await dispose_database()
            configure_database(url)
            if path is None or not path.is_file():
                await init_db()

            from app.db import session as db_session

            async with db_session.async_session_factory() as session:
                total += await delete_test_conversations_in_session(
                    session,
                    database_url=url,
                )
        except Exception:
            logger.exception("Очистка тестовой БД не удалась: %s", url)
        finally:
            await dispose_database()

    return total


async def cleanup_live_test_conversations() -> int:
    """
    Удалить тестовые беседы с работающего сервера (только явный test URL).

  WEB_CHAT_TEST_BASE_URL=http://dev:8090
  WEB_CHAT_TEST_API_KEY=...
    """
    base = resolve_safe_live_cleanup_base_url()
    if not base:
        return 0

    headers: dict[str, str] = {}
    api_key = (os.environ.get("WEB_CHAT_TEST_API_KEY") or "").strip()
    if api_key:
        headers["X-API-Key"] = api_key

    allow_orphans = should_cleanup_orphan_titles_on_live()
    deleted = 0
    async with httpx.AsyncClient(base_url=base, headers=headers, timeout=30.0) as client:
        try:
            resp = await client.get("/api/conversations")
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning(
                "Финишер: не удалось получить список бесед с %s: %s",
                base,
                exc,
            )
            return 0

        targets: dict[str, str] = {}
        for item in resp.json():
            title = item.get("title") or ""
            conv_id = item.get("id")
            if not conv_id:
                continue
            if is_test_conversation_title(title):
                targets[str(conv_id)] = title
            elif allow_orphans and is_likely_test_orphan_title(title):
                targets[str(conv_id)] = title

        for conv_id, title in targets.items():
            try:
                del_resp = await client.delete(f"/api/conversations/{conv_id}")
                if del_resp.status_code in (200, 204, 404):
                    deleted += 1
                else:
                    logger.warning(
                        "Не удалось удалить беседу %s (%s): HTTP %s",
                        conv_id,
                        title,
                        del_resp.status_code,
                    )
            except httpx.HTTPError as exc:
                logger.warning("DELETE беседы %s: %s", conv_id, exc)

    return deleted


def _sqlite_path_from_url(url: str) -> Path | None:
    if "sqlite" not in url:
        return None
    path_part = url.split("///", 1)[-1]
    if path_part.startswith("./"):
        return Path(path_part[2:]).resolve()
    return Path(path_part).resolve()


# Обратная совместимость для CLI
def resolve_live_cleanup_base_url() -> str | None:
    return resolve_safe_live_cleanup_base_url()
