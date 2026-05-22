"""Состояние фоновой генерации для REST/WS (возобновление UI после перезагрузки)."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.ws_manager import manager
from app.db.repositories import MessageRepository


async def get_generation_state(
    session: AsyncSession,
    conversation_id: uuid.UUID,
) -> dict[str, bool | str | None]:
    """
    in_progress — активная задача на сервере.
    streaming_message_id — id черновика assistant (если уже создан).
    """
    in_progress = manager.is_busy(conversation_id)
    streaming_id = manager.get_streaming_message(conversation_id)
    msg_repo = MessageRepository(session)
    draft = None

    keep_id = streaming_id if in_progress else None
    await msg_repo.settle_stale_streaming_assistant_messages(
        conversation_id,
        keep_message_id=keep_id,
    )

    if streaming_id is not None:
        current = await msg_repo.get_by_id(streaming_id)
        cj = current.content_json if current and isinstance(current.content_json, dict) else {}
        if not cj.get("streaming"):
            streaming_id = None
            manager.clear_streaming_message(conversation_id)

    if streaming_id is None:
        draft = await msg_repo.get_streaming_assistant_message(conversation_id)
        if draft is not None:
            streaming_id = draft.id
            if in_progress:
                manager.set_streaming_message(conversation_id, draft.id)
    elif streaming_id is not None:
        draft = await msg_repo.get_by_id(streaming_id)

    phase: str | None = None
    active_tool: str | None = None
    if streaming_id is not None:
        draft = await msg_repo.get_by_id(streaming_id)
    if draft is not None and isinstance(draft.content_json, dict):
        phase = draft.content_json.get("phase")
        active_tool = draft.content_json.get("active_tool")

    return {
        "in_progress": in_progress,
        "streaming_message_id": str(streaming_id) if streaming_id else None,
        "phase": phase,
        "active_tool": active_tool,
    }
