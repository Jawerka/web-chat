"""
Экспорт беседы в Markdown для скачивания или архивации.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MessageRole
from app.db.repositories import ConversationRepository, MessageRepository
from app.services.media_service import MediaService


def _role_label(role: MessageRole) -> str:
    if role == MessageRole.USER:
        return "Пользователь"
    if role == MessageRole.ASSISTANT:
        return "Ассистент"
    return role.value


def _format_timestamp(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def _image_urls(content_json: dict | None) -> list[str]:
    if not content_json:
        return []
    images = content_json.get("images")
    if not isinstance(images, list):
        return []
    urls: list[str] = []
    for item in images:
        if isinstance(item, str) and item.strip():
            urls.append(item.strip())
        elif isinstance(item, dict):
            url = item.get("url") or item.get("preview_url")
            if isinstance(url, str) and url.strip():
                urls.append(url.strip())
    return urls


async def build_conversation_markdown(
    db: AsyncSession,
    conversation_id: uuid.UUID,
) -> str | None:
    """
    Собрать Markdown всей беседы (user/assistant).

    Возвращает None, если беседа не найдена.
    """
    conv_repo = ConversationRepository(db)
    conversation = await conv_repo.get_by_id(conversation_id)
    if conversation is None:
        return None

    msg_repo = MessageRepository(db)
    messages = await msg_repo.list_all_for_conversation(conversation_id)
    media = MediaService(db)

    lines: list[str] = [
        f"# {conversation.title}",
        "",
        f"_Экспорт: {_format_timestamp(datetime.now(UTC))}_",
        "",
    ]

    for message in messages:
        if message.role not in (MessageRole.USER, MessageRole.ASSISTANT):
            continue
        enriched_json, enriched_text = await media.enrich_message_content_json(
            message.content_json,
            conversation_id=conversation_id,
            content_text=message.content_text,
        )
        body = (enriched_text or message.content_text or "").strip()
        lines.append(f"## {_role_label(message.role)} — {_format_timestamp(message.created_at)}")
        lines.append("")
        if body:
            lines.append(body)
            lines.append("")
        for url in _image_urls(
            enriched_json if enriched_json is not None else message.content_json
        ):
            lines.append(f"![image]({url})")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"
