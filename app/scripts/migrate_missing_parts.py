"""
Миграция legacy user-сообщений: восстановить content_json.parts из attachments.

Запуск:
  python -m app.scripts.migrate_missing_parts
  python -m app.scripts.migrate_missing_parts --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import uuid
from typing import Any

from sqlalchemy import select

from app.config import settings
from app.db.models import Attachment, Message, MessageRole
from app.db.session import async_session_factory, configure_database, init_db
from app.services.attachment_service import AttachmentService
from app.services.message_builder import build_user_content

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)


def _has_image_parts(content_json: dict[str, Any] | None) -> bool:
    if not content_json or not isinstance(content_json.get("parts"), list):
        return False
    return any(p.get("type") == "image_url" for p in content_json["parts"])


async def migrate_missing_parts(*, dry_run: bool = False) -> int:
    """Дополнить parts у user-сообщений без image_url, но с image-attachments."""
    configure_database(settings.database_url)
    await init_db()

    updated = 0
    async with async_session_factory() as session:
        result = await session.execute(
            select(Message).where(Message.role == MessageRole.USER),
        )
        messages = list(result.scalars().all())

        for msg in messages:
            if _has_image_parts(msg.content_json):
                continue

            att_result = await session.execute(
                select(Attachment).where(Attachment.message_id == msg.id),
            )
            attachments = list(att_result.scalars().all())
            image_atts = [a for a in attachments if a.mime_type.startswith("image/")]
            if not image_atts:
                continue

            text = (msg.content_text or "").strip()
            parts = build_user_content(text, image_atts)
            if dry_run:
                logger.info(
                    "dry-run: message %s → %d parts (%d images)",
                    msg.id,
                    len(parts),
                    len(image_atts),
                )
            else:
                msg.content_json = {"parts": parts}
                updated += 1
                logger.info(
                    "updated message %s: %d image part(s)",
                    msg.id,
                    len(image_atts),
                )

        if not dry_run and updated:
            await session.commit()

    logger.info("Готово: обновлено %d сообщений", updated)
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(description="Восстановить parts из attachments")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только показать, что будет изменено",
    )
    args = parser.parse_args()
    count = asyncio.run(migrate_missing_parts(dry_run=args.dry_run))
    return 0 if count >= 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
