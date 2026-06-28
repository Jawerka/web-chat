"""
CLI: удалить pending-вложения (message_id IS NULL) в беседах, где уже есть сообщения.

Сироты от handoff / снятого чипа — после фикса ghost composer не показываются в UI,
но могли накопиться в БД до деплоя.
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from sqlalchemy import select

from app.config import settings
from app.db import session as db_session
from app.db.models import Attachment, Message
from app.db.repositories import AttachmentRepository
from app.db.session import configure_database, init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)


async def _conversation_ids_with_messages(session) -> set:
    result = await session.execute(select(Message.conversation_id).distinct())
    return {row[0] for row in result.all() if row[0] is not None}


async def run(*, dry_run: bool) -> int:
    configure_database(settings.database_url)
    await init_db()
    removed = 0
    async with db_session.async_session_factory() as session:
        conv_with_msgs = await _conversation_ids_with_messages(session)
        if not conv_with_msgs:
            logger.info("Нет бесед с сообщениями — нечего чистить")
            return 0

        result = await session.execute(
            select(Attachment).where(
                Attachment.message_id.is_(None),
                Attachment.conversation_id.in_(conv_with_msgs),
            ),
        )
        orphans = list(result.scalars().all())
        if not orphans:
            logger.info("Сиротных pending-вложений не найдено")
            return 0

        if dry_run:
            logger.info("dry-run: найдено %d сирот в %d беседах", len(orphans), len(conv_with_msgs))
            for att in orphans[:20]:
                logger.info("  attachment %s conv=%s", att.id, att.conversation_id)
            return len(orphans)

        att_repo = AttachmentRepository(session)
        by_conv: dict = {}
        for att in orphans:
            if att.conversation_id is not None:
                by_conv.setdefault(att.conversation_id, 0)
                by_conv[att.conversation_id] += 1

        for conv_id in by_conv:
            removed += await att_repo.delete_pending_for_conversation(conv_id)

        await session.commit()
        logger.info("Удалено pending-вложений: %d (бесед: %d)", removed, len(by_conv))
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description="Cleanup orphan pending attachments")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только показать количество, без удаления",
    )
    args = parser.parse_args()
    asyncio.run(run(dry_run=args.dry_run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
