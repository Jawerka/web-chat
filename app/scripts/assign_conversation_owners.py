#!/usr/bin/env python3
"""
Назначить owner_user_id беседам без владельца (P2.2).

Перед включением MULTI_USER_ENABLED=true — иначе legacy-беседы не видны в списке.

  python -m app.scripts.assign_conversation_owners --user default
  python -m app.scripts.assign_conversation_owners --user alice --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from dotenv import load_dotenv

load_dotenv()

from app.db.repositories import ConversationRepository, UserRepository  # noqa: E402
from app.db.session import async_session_factory, init_db  # noqa: E402
from app.logging_setup import setup_logging  # noqa: E402
from app.services.request_user import _normalize_user_slug  # noqa: E402

setup_logging()
logger = logging.getLogger(__name__)


async def run_assign(*, user_slug: str, dry_run: bool) -> int:
    slug = _normalize_user_slug(user_slug)
    async with async_session_factory() as session:
        orphans = await ConversationRepository(session).count_orphans()
        if orphans == 0:
            logger.info("Нет бесед без владельца (owner_user_id IS NULL)")
            return 0

        user_repo = UserRepository(session)
        user = await user_repo.get_by_login(slug)
        if user is None and dry_run:
            logger.info(
                "Dry-run: будет создан пользователь %r, назначено бесед: %d",
                slug,
                orphans,
            )
            return orphans

        if user is None:
            from app.db.models import UserRole
            from app.security.passwords import hash_password

            user = await user_repo.create_user(
                login=slug,
                slug=slug,
                display_name=slug,
                password_hash=hash_password("changeme"),
                role=UserRole.USER,
            )
            logger.info(
                "Создан пользователь %s (%s) — задайте пароль через смену в БД или пересоздайте admin",
                slug,
                user.id,
            )

        if dry_run:
            logger.info(
                "Dry-run: пользователь %r (%s) получит %d бесед(ы)",
                slug,
                user.id,
                orphans,
            )
            return orphans

        assigned = await ConversationRepository(session).assign_orphan_conversations(
            user.id,
        )
        await session.commit()
        logger.info(
            "Назначено %d бесед пользователю %r (%s)",
            assigned,
            slug,
            user.id,
        )
        return assigned


async def main_async(args: argparse.Namespace) -> int:
    await init_db()
    try:
        count = await run_assign(user_slug=args.user, dry_run=args.dry_run)
    except ValueError as exc:
        logger.error("%s", exc)
        return 1
    return 0 if count >= 0 else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Назначить owner_user_id беседам без владельца",
    )
    parser.add_argument(
        "--user",
        default="default",
        help="Slug пользователя (как X-Web-Chat-User), по умолчанию default",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только показать, сколько бесед будет назначено",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
