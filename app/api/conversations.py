"""
REST API бесед: CRUD без WebSocket.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    ConversationCreate,
    ConversationOut,
    ConversationUpdate,
    TurnCreate,
    TurnStartedOut,
)
from app.api.ws_manager import manager
from app.integrations.runtime_config import IntegrationOverrides, parse_optional_url
from app.services.prompt_macro_service import parse_macro_context_mode
from app.services.generation_state import get_generation_state
from app.constants import DEFAULT_CONVERSATION_TITLE
from app.db.repositories import ConversationRepository, PresetRepository
from app.db.session import get_db
from app.services.conversation_export_service import build_conversation_markdown

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("", response_model=list[ConversationOut])
async def list_conversations(
    db: AsyncSession = Depends(get_db),
) -> list[ConversationOut]:
    """Список бесед, сортировка updated_at DESC."""
    repo = ConversationRepository(db)
    conversations = await repo.list_all()
    busy = manager.busy_conversation_ids()
    result: list[ConversationOut] = []
    for c in conversations:
        item = ConversationOut.model_validate(c)
        item.in_progress = c.id in busy
        result.append(item)
    return result


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


@router.get("/{conversation_id}/generation-status")
async def generation_status(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Состояние фоновой генерации (для возобновления UI после перезагрузки)."""
    return await get_generation_state(db, conversation_id)


@router.get("/{conversation_id}/llm-context")
async def get_llm_context(
    conversation_id: uuid.UUID,
    macro_context: str | None = None,
    max_messages: int | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
  Контекст для LLM, собранный из SQLite (переживает рестарт сервера).

  То же, что уйдёт в модель при следующем ходе (без нового user-сообщения).
  """
    from app.services.llm_context import build_conversation_llm_context

    conv_repo = ConversationRepository(db)
    if await conv_repo.get_by_id(conversation_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Беседа не найдена",
        )
    try:
        return await build_conversation_llm_context(
            db,
            conversation_id,
            macro_context=macro_context,
            max_messages=max_messages,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.post(
    "/{conversation_id}/turn",
    response_model=TurnStartedOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_conversation_turn(
    conversation_id: uuid.UUID,
    body: TurnCreate,
    db: AsyncSession = Depends(get_db),
) -> TurnStartedOut:
    """
    Запустить ход агента из внешнего приложения (без WebSocket).

    User-сообщение сохранится в БД; прогресс — GET generation-status и messages.
    При открытом WS клиенте события стрима также уйдут в сокет.
    """
    from app.api.websocket import _schedule_turn_task, _start_background_turn

    conv_repo = ConversationRepository(db)
    if await conv_repo.get_by_id(conversation_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Беседа не найдена",
        )
    if manager.is_busy(conversation_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Уже выполняется генерация",
        )

    integration = IntegrationOverrides(
        llm_model=body.model.strip() if body.model else None,
        llm_base_url=parse_optional_url(body.llm_base_url),
        sd_webui_url=parse_optional_url(body.sd_webui_url),
        macro_context=parse_macro_context_mode(body.macro_context),
    )
    _start_background_turn(
        conversation_id,
        _schedule_turn_task(
            conversation_id,
            body.text.strip(),
            list(body.attachment_ids),
            integration,
        ),
        turn_kind="api_turn",
    )
    return TurnStartedOut(conversation_id=conversation_id)


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


@router.get("/{conversation_id}/export")
async def export_conversation(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> PlainTextResponse:
    """Скачать беседу как Markdown."""
    markdown = await build_conversation_markdown(db, conversation_id)
    if markdown is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Беседа не найдена",
        )
    conv_repo = ConversationRepository(db)
    conversation = await conv_repo.get_by_id(conversation_id)
    assert conversation is not None
    filename = f"conversation-{conversation_id}.md"
    return PlainTextResponse(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


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
