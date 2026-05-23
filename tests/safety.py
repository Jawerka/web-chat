"""
Безопасность тестов: изоляция от production SQLite и живых бесед пользователя.

Правила:
- unit/integration тесты работают только с временной SQLite (фикстура client / tmp_path);
- финишер pytest не удаляет production DB и не трогает «чужие» заголовки на live;
- HTTP-очистка live — только при явном WEB_CHAT_TEST_BASE_URL.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def _normalize_db_url(url: str) -> str:
    u = url.strip()
    if "///" in u:
        return u.split("///", 1)[-1]
    lower = u.lower()
    for prefix in (
        "postgresql+asyncpg://",
        "postgresql+psycopg2://",
        "postgresql+psycopg://",
        "postgresql://",
    ):
        if lower.startswith(prefix):
            return "postgresql://" + u[len(prefix) :]
    if lower.startswith("postgres://"):
        return "postgresql://" + u[len("postgres://") :]
    return u


def production_database_url() -> str:
    from app.config import settings

    return settings.database_url


def production_database_path() -> Path:
    url = production_database_url()
    if "sqlite" not in url:
        msg = f"Ожидалась SQLite production БД, получено: {url}"
        raise RuntimeError(msg)
    part = _normalize_db_url(url)
    if part.startswith("./"):
        return Path(part[2:]).resolve()
    return Path(part).resolve()


def is_production_database_url(url: str) -> bool:
    """True, если URL указывает на рабочую БД из .env (DATABASE_URL)."""
    return _normalize_db_url(url) == _normalize_db_url(production_database_url())


def is_safe_test_database_url(url: str) -> bool:
    """Можно ли чистить эту SQLite после pytest (только не-production tmp)."""
    if "sqlite" not in url.lower():
        return False
    return not is_production_database_url(url)


def assert_not_using_production_database() -> None:
    """Вызвать в тесте: запрет доступа к production engine."""
    from app.db.session import engine

    if engine is None:
        return
    current = str(engine.url)
    if is_production_database_url(current):
        msg = (
            "Тест подключён к production БД (DATABASE_URL из .env). "
            "Используйте фикстуру client, safe_configure_database() "
            "или db_session.async_session_factory() после configure tmp DB."
        )
        raise RuntimeError(msg)


def safe_configure_database(database_url: str) -> None:
    """configure_database только для временной SQLite + запись в cleanup."""
    if not is_safe_test_database_url(database_url):
        msg = f"Отказ: небезопасный URL тестовой БД: {database_url}"
        raise RuntimeError(msg)
    from app.db.session import configure_database

    from tests.cleanup import record_test_database_url

    configure_database(database_url)
    record_test_database_url(database_url)


def live_cleanup_explicitly_enabled() -> bool:
    """HTTP-очистка разрешена только при явном WEB_CHAT_TEST_BASE_URL."""
    return bool((os.environ.get("WEB_CHAT_TEST_BASE_URL") or "").strip())


def public_live_cleanup_allowed() -> bool:
    """
    Разрешить очистку через PUBLIC_BASE_URL (WEB_CHAT_TEST_CLEANUP_LIVE=1).

    По умолчанию выключено — нужен WEB_CHAT_TEST_ALLOW_PUBLIC_CLEANUP=1.
    """
    flag = (os.environ.get("WEB_CHAT_TEST_ALLOW_PUBLIC_CLEANUP") or "").strip().lower()
    return flag in ("1", "true", "yes", "on")


def resolve_safe_live_cleanup_base_url() -> str | None:
    """
    URL для HTTP-удаления после pytest.

    1. WEB_CHAT_TEST_BASE_URL — явный тестовый инстанс (рекомендуется)
    2. Иначе PUBLIC_BASE_URL только при CLEANUP_LIVE + ALLOW_PUBLIC_CLEANUP
    """
    explicit = (os.environ.get("WEB_CHAT_TEST_BASE_URL") or "").strip().rstrip("/")
    if explicit:
        return explicit

    live_flag = (os.environ.get("WEB_CHAT_TEST_CLEANUP_LIVE") or "").strip().lower()
    if live_flag not in ("1", "true", "yes", "on"):
        return None

    if not public_live_cleanup_allowed():
        logger.warning(
            "WEB_CHAT_TEST_CLEANUP_LIVE проигнорирован: задайте WEB_CHAT_TEST_BASE_URL "
            "или WEB_CHAT_TEST_ALLOW_PUBLIC_CLEANUP=1 для очистки через PUBLIC_BASE_URL",
        )
        return None

    from app.config import settings

    return settings.public_base_url.rstrip("/")
