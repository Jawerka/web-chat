"""
WD14-теги только для user-вложений текущего хода (attachment_ids).
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Attachment
from app.integrations.media_utils import is_image_mime
from app.integrations.wd_tagger_service import wd_tagger_service
from app.services.media_service import MediaService
from app.services.message_builder import Wd14TagEntry

logger = logging.getLogger(__name__)


async def tag_user_attachments(
    session: AsyncSession,
    attachments: list[Attachment],
) -> list[Wd14TagEntry]:
    """
    Распознать теги WD14 для image-вложений пользователя в этом ходе.

    Не вызывается для истории, assistant images или SD-генераций.
    """
    if not settings.wd_tagger_enabled:
        return []

    media = MediaService(session)
    entries: list[Wd14TagEntry] = []

    for att in attachments:
        if not is_image_mime(att.mime_type):
            continue
        if att.media_asset_id is None:
            logger.warning("WD14 skip attachment without media_asset_id: %s", att.id)
            continue
        try:
            result = await media.get_bytes(att.media_asset_id, trusted_internal=True)
        except Exception:
            logger.warning("WD14 failed to load bytes for %s", att.media_asset_id, exc_info=True)
            continue
        if result is None:
            logger.warning("WD14 media asset missing: %s", att.media_asset_id)
            continue
        data, mime = result
        tags = await wd_tagger_service.tag_bytes(data, mime)
        entries.append(
            Wd14TagEntry(
                attachment_id=str(att.id),
                filename=att.original_name or "image",
                tags=tags,
            ),
        )
        logger.info(
            "WD14 tagged attachment %s (%s): %d chars",
            att.id,
            att.original_name,
            len(tags),
        )

    return entries
