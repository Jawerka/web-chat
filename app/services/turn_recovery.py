"""
Восстановление частичного ответа при ошибке или отмене turn.

Сохраняет уже показанный пользователю текст/картинки вместо полного отката черновика.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.ws_manager import manager
from app.db.models import MessageRole
from app.db.repositories import ConversationRepository, MessageRepository
from app.services.turn_status import patch_interrupted

logger = logging.getLogger(__name__)


def _has_visible_content(content_text: str | None, content_json: dict[str, Any] | None) -> bool:
    if (content_text or "").strip():
        return True
    if not isinstance(content_json, dict):
        return False
    if content_json.get("images"):
        return True
    if content_json.get("image_asset_ids"):
        return True
    return False


async def settle_interrupted_turn(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    *,
    status_code: str,
    status_message: str | None = None,
) -> bool:
    """
    Зафиксировать или удалить прерванный черновик ассистента.

    Returns:
        True, если черновик сохранён (partial/failed/cancelled).
    """
    msg_repo = MessageRepository(session)
    conv_repo = ConversationRepository(session)
    streaming_id = manager.get_streaming_message(conversation_id)

    target = None
    if streaming_id is not None:
        target = await msg_repo.get_by_id(streaming_id)

    if target is None:
        last = await msg_repo.get_last_message(conversation_id)
        if (
            last is not None
            and last.role == MessageRole.ASSISTANT
            and isinstance(last.content_json, dict)
            and last.content_json.get("streaming")
        ):
            target = last

    if target is None:
        manager.clear_streaming_message(conversation_id)
        return False

    payload: dict[str, Any] = (
        dict(target.content_json) if isinstance(target.content_json, dict) else {}
    )

    if not _has_visible_content(target.content_text, payload):
        await msg_repo.delete(target)
        conv = await conv_repo.get_by_id(conversation_id)
        if conv is not None:
            await conv_repo.touch(conv)
        manager.clear_streaming_message(conversation_id)
        logger.info(
            "Прерванный черновик %s удалён (пустой), conv=%s",
            target.id,
            conversation_id,
        )
        return False

    payload = patch_interrupted(
        payload,
        status_code=status_code,
        status_message=status_message,
    )

    text = (target.content_text or "").strip()
    if status_code in ("failed", "tool_loop", "llm_error", "internal") and status_message:
        if status_message not in text:
            text = f"{text}\n\n{status_message}".strip() if text else status_message

    await msg_repo.update_content(
        target,
        content_text=text,
        content_json=payload,
    )
    conv = await conv_repo.get_by_id(conversation_id)
    if conv is not None:
        await conv_repo.touch(conv)
    manager.clear_streaming_message(conversation_id)
    logger.info(
        "Черновик %s зафиксирован как %s (conv=%s)",
        target.id,
        status_code,
        conversation_id,
    )
    return True
