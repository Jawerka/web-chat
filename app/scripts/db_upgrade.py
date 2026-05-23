#!/usr/bin/env python3
"""
Применить миграции Alembic (Postgres или SQLite).

  python -m app.scripts.db_upgrade
  python -m app.scripts.db_upgrade --revision head
"""

from __future__ import annotations

import argparse
import logging

from dotenv import load_dotenv

load_dotenv()

from app.db.alembic_runner import run_alembic_upgrade  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Alembic upgrade для web-chat")
    parser.add_argument(
        "--revision",
        default="head",
        help="Целевая ревизия (по умолчанию head)",
    )
    args = parser.parse_args()
    run_alembic_upgrade(args.revision)
    logger.info("Готово: %s", args.revision)


if __name__ == "__main__":
    main()
