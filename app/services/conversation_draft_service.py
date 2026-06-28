"""Сборка ответов API с серверным черновиком composer."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import AttachmentOut, ConversationDetailOut, ConversationOut
from app.db.models import Conversation
from app.db.repositories import AttachmentRepository, MessageRepository
from app.services.attachment_service import AttachmentService


def attachment_out(service: AttachmentService, attachment) -> AttachmentOut:
    return AttachmentOut(
        id=attachment.id,
        original_name=attachment.original_name,
        mime_type=attachment.mime_type,
        size_bytes=attachment.size_bytes,
        preview_url=service.preview_url(attachment),
    )


async def build_conversation_detail_out(
    session: AsyncSession,
    conversation: Conversation,
    *,
    in_progress: bool = False,
) -> ConversationDetailOut:
    """Беседа + composer_text + pending attachments (только для пустых бесед)."""
    msg_repo = MessageRepository(session)
    message_count = await msg_repo.count_for_conversation(conversation.id)

    att_repo = AttachmentRepository(session)
    att_service = AttachmentService(session)

    if message_count > 0:
        composer_text = ""
        pending_out: list[AttachmentOut] = []
    else:
        pending = await att_repo.list_pending_for_conversation(conversation.id)
        composer_text = conversation.composer_draft_text or ""
        pending_out = [attachment_out(att_service, a) for a in pending]

    base = ConversationOut.model_validate(conversation)
    base.in_progress = in_progress
    return ConversationDetailOut(
        **base.model_dump(),
        message_count=message_count,
        composer_text=composer_text,
        pending_attachments=pending_out,
    )


def chat_url_for_conversation(conversation_id: uuid.UUID) -> str:
    return f"/?conv={conversation_id}"
