"""
Индекс ссылок на MediaAsset в сообщениях и вложениях (P2.4).
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Attachment, Message

_ASSET_PATH_RE = re.compile(
    r"/media/asset/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})",
    re.IGNORECASE,
)
def _parse_uuid(value: Any) -> uuid.UUID | None:
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


def _collect_ids_from_content_json(cj: dict[str, Any] | None, into: set[uuid.UUID]) -> None:
    if not isinstance(cj, dict):
        return
    for raw in cj.get("image_asset_ids") or []:
        aid = _parse_uuid(raw)
        if aid:
            into.add(aid)
    for url in cj.get("images") or []:
        for match in _ASSET_PATH_RE.finditer(str(url)):
            aid = _parse_uuid(match.group(1))
            if aid:
                into.add(aid)
    parts = cj.get("parts")
    if isinstance(parts, list):
        for part in parts:
            if not isinstance(part, dict):
                continue
            aid = _parse_uuid(part.get("asset_id"))
            if aid:
                into.add(aid)
            if part.get("type") == "image_url":
                url = (part.get("image_url") or {}).get("url", "")
                for match in _ASSET_PATH_RE.finditer(str(url)):
                    parsed = _parse_uuid(match.group(1))
                    if parsed:
                        into.add(parsed)


def _collect_ids_from_text(text: str | None, into: set[uuid.UUID]) -> None:
    if not text:
        return
    for match in _ASSET_PATH_RE.finditer(text):
        aid = _parse_uuid(match.group(1))
        if aid:
            into.add(aid)


async def collect_referenced_asset_ids(session: AsyncSession) -> set[uuid.UUID]:
    """Все MediaAsset, на которые есть ссылки в attachments или messages."""
    referenced: set[uuid.UUID] = set()

    att_rows = await session.execute(
        select(Attachment.media_asset_id).where(Attachment.media_asset_id.is_not(None)),
    )
    for (asset_id,) in att_rows.all():
        if asset_id is not None:
            referenced.add(asset_id)

    msg_rows = await session.execute(
        select(Message.content_text, Message.content_json),
    )
    for content_text, content_json in msg_rows.all():
        _collect_ids_from_text(content_text, referenced)
        _collect_ids_from_content_json(content_json, referenced)

    return referenced
