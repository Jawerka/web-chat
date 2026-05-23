#!/usr/bin/env python3
"""
ETL: перенос данных из SQLite (LAN) в PostgreSQL.

Перед запуском: остановите web-chat или работайте с копией файла БД.

  cp data/db/web_chat.sqlite data/db/web_chat.sqlite.bak
  export DATABASE_URL=postgresql+asyncpg://webchat:SECRET@127.0.0.1:5432/web_chat
  python -m app.scripts.migrate_sqlite_to_postgres \\
    --source sqlite+aiosqlite:///./data/db/web_chat.sqlite.bak \\
    --target "$DATABASE_URL" \\
    --truncate-target --yes

  python -m app.scripts.migrate_sqlite_to_postgres --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from app.config import settings  # noqa: E402
from app.db.etl_sqlite_to_postgres import EtlError, EtlOptions, run_etl  # noqa: E402
from app.db.url import is_postgres_url, is_sqlite_url  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _default_source() -> str:
    url = settings.database_url
    if not is_sqlite_url(url):
        raise EtlError(
            "DATABASE_URL в .env не SQLite — укажите --source явно "
            "(путь к web_chat.sqlite)",
        )
    return url


def _resolve_target(explicit: str | None) -> str:
    import os

    target = (explicit or os.environ.get("MIGRATE_TARGET_URL") or "").strip()
    if not target:
        if is_postgres_url(settings.database_url):
            return settings.database_url
        raise EtlError(
            "Укажите --target или MIGRATE_TARGET_URL (postgresql+asyncpg://…)",
        )
    return target


def _warn_production_source(path: str) -> None:
    """Предупреждение, если источник — живая production SQLite."""
    if "///" not in path:
        return
    file_part = path.split("///", 1)[-1]
    if file_part.startswith("./"):
        file_part = file_part[2:]
    resolved = Path(file_part).resolve()
    prod = Path("data/db/web_chat.sqlite").resolve()
    if resolved == prod:
        logger.warning(
            "Источник — активный файл БД (%s). Рекомендуется копия (.bak) "
            "и остановленный сервис.",
            resolved,
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="ETL SQLite → PostgreSQL для web-chat",
    )
    parser.add_argument(
        "--source",
        default=None,
        help="SQLite URL (по умолчанию DATABASE_URL из .env)",
    )
    parser.add_argument(
        "--target",
        default=None,
        help="Postgres URL (или MIGRATE_TARGET_URL / DATABASE_URL если Postgres)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только подсчёт строк, без записи",
    )
    parser.add_argument(
        "--truncate-target",
        action="store_true",
        help="Очистить приёмник (TRUNCATE CASCADE) перед вставкой",
    )
    parser.add_argument(
        "--skip-media",
        action="store_true",
        help="Не копировать media_assets (BLOB); картинки в чате не заработают",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Размер батча (для media_assets лучше 20–50)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Подтвердить запись (без --dry-run обязателен)",
    )
    args = parser.parse_args()

    try:
        source = args.source or _default_source()
        target = _resolve_target(args.target)
        _warn_production_source(source)

        if not args.dry_run and not args.yes:
            logger.error("Для записи добавьте --yes (или используйте --dry-run)")
            return 1

        if not args.dry_run and not is_postgres_url(target):
            logger.error("Приёмник должен быть PostgreSQL: %s", target)
            return 1

        batch = args.batch_size
        if not args.skip_media and batch > 50:
            batch = min(batch, 50)

        options = EtlOptions(
            source_url=source,
            target_url=target,
            dry_run=args.dry_run,
            truncate_target=args.truncate_target,
            skip_media_assets=args.skip_media,
            batch_size=batch,
        )
        stats = asyncio.run(run_etl(options))

        print("--- ETL summary ---")
        for key, label in (
            ("source", "Источник"),
            ("target_before", "Приёмник (до)"),
            ("copied", "Скопировано"),
            ("target_after", "Приёмник (после)"),
        ):
            print(f"{label}:")
            for table, n in stats.as_dict().get(key, {}).items():
                print(f"  {table}: {n}")

        if args.dry_run:
            print("\n(dry-run — данные не изменены)")
        else:
            print("\nГотово. Переключите DATABASE_URL на Postgres и перезапустите сервис.")
        return 0

    except EtlError as exc:
        logger.error("%s", exc)
        return 1
    except Exception:
        logger.exception("ETL failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
