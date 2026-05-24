"""
REST API истории и управления сообщениями беседы.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import AttachmentOut, MessageOut, MessageUpdate
from app.db.models import MessageRole
from app.db.repositories import AttachmentRepository, MessageRepository
from app.db.session import get_db
from app.services.attachment_service import AttachmentService
from app.services.conversation_access import get_accessible_conversation
from app.services.request_user import RequestUser, get_request_user
from app.services.media_service import MediaService
from app.services.message_builder import build_user_content, strip_img2img_gen_preset_prefix

router = APIRouter(prefix="/conversations", tags=["messages"])


def _message_out(m) -> MessageOut:
    content_text = m.content_text
    if m.role == MessageRole.USER and content_text:
        content_text = strip_img2img_gen_preset_prefix(content_text)
    return MessageOut(
        id=m.id,
        conversation_id=m.conversation_id,
        role=m.role.value,
        content_text=content_text,
        content_json=m.content_json,
        created_at=m.created_at,
    )


@router.get("/{conversation_id}/messages", response_model=list[MessageOut])
async def list_messages(
    conversation_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    before: uuid.UUID | None = Query(None, description="Cursor: id сообщения"),
    db: AsyncSession = Depends(get_db),
    user: RequestUser | None = Depends(get_request_user),
) -> list[MessageOut]:
    """История сообщений с пагинацией (before = старше указанного id)."""
    if await get_accessible_conversation(db, conversation_id, user) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Беседа не найдена",
        )

    msg_repo = MessageRepository(db)
    if before is None:
        from app.api.ws_manager import manager

        keep_id = (
            manager.get_streaming_message(conversation_id)
            if manager.is_busy(conversation_id)
            else None
        )
        await msg_repo.settle_stale_streaming_assistant_messages(
            conversation_id,
            keep_message_id=keep_id,
        )
    messages = await msg_repo.list_for_conversation(
        conversation_id,
        limit=limit,
        before_id=before,
    )
    media = MediaService(db)
    out: list[MessageOut] = []
    for m in messages:
        enriched_json, enriched_text = await media.enrich_message_content_json(
            m.content_json,
            conversation_id=conversation_id,
            content_text=m.content_text,
        )
        json_changed = enriched_json is not None and enriched_json != m.content_json
        text_changed = enriched_text is not None and enriched_text != m.content_text
        if json_changed or text_changed:
            m = await msg_repo.update_content(
                m,
                content_text=enriched_text if enriched_text is not None else m.content_text,
                content_json=enriched_json if enriched_json is not None else m.content_json,
            )
        out.append(_message_out(m))
    return out


@router.get(
    "/{conversation_id}/messages/{message_id}/attachments",
    response_model=list[AttachmentOut],
)
async def list_message_attachments(
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> list[AttachmentOut]:
    """Вложения сообщения (для редактирования user-сообщения в UI)."""
    msg_repo = MessageRepository(db)
    message = await msg_repo.get_by_id(message_id)
    if message is None or message.conversation_id != conversation_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Сообщение не найдено")

    att_repo = AttachmentRepository(db)
    service = AttachmentService(db)
    attachments = await att_repo.list_for_message(message_id)
    return [
        AttachmentOut(
            id=att.id,
            original_name=att.original_name,
            mime_type=att.mime_type,
            size_bytes=att.size_bytes,
            preview_url=service.preview_url(att),
        )
        for att in attachments
    ]


@router.patch("/{conversation_id}/messages/{message_id}", response_model=MessageOut)
async def update_message(
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    body: MessageUpdate,
    db: AsyncSession = Depends(get_db),
) -> MessageOut:
    """Изменить текст сообщения (user или assistant)."""
    msg_repo = MessageRepository(db)
    message = await msg_repo.get_by_id(message_id)
    if message is None or message.conversation_id != conversation_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Сообщение не найдено")

    text = body.content_text.strip()
    if not text:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Пустой текст")

    if message.role == MessageRole.USER:
        text = strip_img2img_gen_preset_prefix(text)

    content_json = message.content_json
    if message.role == MessageRole.USER:
        att_repo = AttachmentRepository(db)
        if body.attachment_ids is not None:
            try:
                await att_repo.sync_message_attachments(
                    message_id,
                    conversation_id,
                    body.attachment_ids,
                )
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=str(exc),
                ) from exc
        attachments = await att_repo.list_for_message(message_id)
        parts = build_user_content(text, attachments)
        content_json = {"parts": parts}

    message = await msg_repo.update_content(
        message,
        content_text=text,
        content_json=content_json,
    )
    return _message_out(message)


@router.delete("/{conversation_id}/messages/{message_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_message(
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    cascade: bool = Query(
        True,
        description="Удалить также все сообщения после этого",
    ),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Удалить сообщение; при cascade — и все последующие."""
    msg_repo = MessageRepository(db)
    message = await msg_repo.get_by_id(message_id)
    if message is None or message.conversation_id != conversation_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Сообщение не найдено")

    if cascade:
        await msg_repo.delete_message_and_following(message)
    else:
        await msg_repo.delete(message)
