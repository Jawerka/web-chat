"""
REST API бесед: CRUD без WebSocket.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import ConversationCreate, ConversationOut, ConversationUpdate
from app.constants import DEFAULT_CONVERSATION_TITLE
from app.db.repositories import ConversationRepository, PresetRepository
from app.db.session import get_db

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("", response_model=list[ConversationOut])
async def list_conversations(
    db: AsyncSession = Depends(get_db),
) -> list[ConversationOut]:
    """Список бесед, сортировка updated_at DESC."""
    repo = ConversationRepository(db)
    conversations = await repo.list_all()
    return [ConversationOut.model_validate(c) for c in conversations]


@router.post("", response_model=ConversationOut, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    body: ConversationCreate,
    db: AsyncSession = Depends(get_db),
) -> ConversationOut:
    """Создать беседу; preset_id опционален — берётся default."""
    preset_repo = PresetRepository(db)
    if body.preset_id is not None:
        preset = await preset_repo.get_by_id(body.preset_id)
        if preset is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Пресет не найден",
            )
    else:
        preset = await preset_repo.get_default()
        if preset is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Не настроен пресет по умолчанию",
            )

    title = body.title.strip() if body.title and body.title.strip() else DEFAULT_CONVERSATION_TITLE
    conv_repo = ConversationRepository(db)
    conversation = await conv_repo.create(title=title, preset_id=preset.id)
    return ConversationOut.model_validate(conversation)


@router.get("/{conversation_id}", response_model=ConversationOut)
async def get_conversation(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ConversationOut:
    """Одна беседа по id."""
    repo = ConversationRepository(db)
    conversation = await repo.get_by_id(conversation_id)
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Беседа не найдена",
        )
    return ConversationOut.model_validate(conversation)


@router.patch("/{conversation_id}", response_model=ConversationOut)
async def update_conversation(
    conversation_id: uuid.UUID,
    body: ConversationUpdate,
    db: AsyncSession = Depends(get_db),
) -> ConversationOut:
    """Обновить заголовок и/или пресет беседы."""
    conv_repo = ConversationRepository(db)
    conversation = await conv_repo.get_by_id(conversation_id)
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Беседа не найдена",
        )

    if body.preset_id is not None:
        preset_repo = PresetRepository(db)
        preset = await preset_repo.get_by_id(body.preset_id)
        if preset is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Пресет не найден",
            )

    title = body.title.strip() if body.title is not None and body.title.strip() else body.title
    conversation = await conv_repo.update(
        conversation,
        title=title,
        preset_id=body.preset_id,
    )
    return ConversationOut.model_validate(conversation)


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Удалить беседу и связанные сообщения."""
    repo = ConversationRepository(db)
    conversation = await repo.get_by_id(conversation_id)
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Беседа не найдена",
        )
    await repo.delete(conversation)
