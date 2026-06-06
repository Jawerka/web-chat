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
    GenerateTitleCreate,
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
from app.services.conversation_access import get_accessible_conversation
from app.services.conversation_export_service import build_conversation_markdown
from app.services.request_user import RequestUser, get_request_user, owner_user_id_for_request
from app.services.user_quotas import ensure_can_create_conversation

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("", response_model=list[ConversationOut])
async def list_conversations(
    db: AsyncSession = Depends(get_db),
    user: RequestUser | None = Depends(get_request_user),
) -> list[ConversationOut]:
    """Список бесед, сортировка updated_at DESC."""
    repo = ConversationRepository(db)
    conversations = await repo.list_all(
        owner_user_id=owner_user_id_for_request(user),
    )
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
    user: RequestUser | None = Depends(get_request_user),
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
    await ensure_can_create_conversation(db, user)
    conv_repo = ConversationRepository(db)
    conversation = await conv_repo.create(
        title=title,
        preset_id=preset.id,
        owner_user_id=owner_user_id_for_request(user),
    )
    await db.commit()
    return ConversationOut.model_validate(conversation)


@router.get("/trash", response_model=list[ConversationOut])
async def list_trash_conversations(
    db: AsyncSession = Depends(get_db),
    user: RequestUser | None = Depends(get_request_user),
) -> list[ConversationOut]:
    """Беседы в корзине (хранятся trash_retention_days, затем удаляются)."""
    repo = ConversationRepository(db)
    rows = await repo.list_trash(owner_user_id=owner_user_id_for_request(user))
    return [ConversationOut.model_validate(c) for c in rows]


@router.delete("/trash")
async def empty_trash(
    db: AsyncSession = Depends(get_db),
    user: RequestUser | None = Depends(get_request_user),
) -> dict[str, int]:
    """Окончательно удалить все беседы в корзине текущего пользователя."""
    repo = ConversationRepository(db)
    deleted = await repo.empty_trash_for_owner(
        owner_user_id=owner_user_id_for_request(user),
    )
    await db.commit()
    return {"deleted": deleted}


@router.post("/{conversation_id}/restore", response_model=ConversationOut)
async def restore_conversation(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: RequestUser | None = Depends(get_request_user),
) -> ConversationOut:
    """Восстановить беседу из корзины."""
    repo = ConversationRepository(db)
    conversation = await repo.get_by_id_for_owner(
        conversation_id,
        owner_user_id=owner_user_id_for_request(user),
        include_deleted=True,
    )
    if conversation is None or conversation.deleted_at is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Беседа не найдена в корзине",
        )
    conversation = await repo.restore_from_trash(conversation)
    await db.commit()
    return ConversationOut.model_validate(conversation)


@router.delete("/{conversation_id}/permanent", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation_permanent(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: RequestUser | None = Depends(get_request_user),
) -> None:
    """Окончательно удалить беседу из корзины."""
    repo = ConversationRepository(db)
    conversation = await repo.get_by_id_for_owner(
        conversation_id,
        owner_user_id=owner_user_id_for_request(user),
        include_deleted=True,
    )
    if conversation is None or conversation.deleted_at is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Беседа не найдена в корзине",
        )
    await repo.delete_permanent(conversation)
    await db.commit()


@router.get("/{conversation_id}/generation-status")
async def generation_status(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: RequestUser | None = Depends(get_request_user),
) -> dict:
    """Состояние фоновой генерации (для возобновления UI после перезагрузки)."""
    if await get_accessible_conversation(db, conversation_id, user) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Беседа не найдена",
        )
    return await get_generation_state(db, conversation_id)


@router.get("/{conversation_id}/llm-context")
async def get_llm_context(
    conversation_id: uuid.UUID,
    macro_context: str | None = None,
    max_messages: int | None = None,
    q: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: RequestUser | None = Depends(get_request_user),
) -> dict:
    """
  Контекст для LLM, собранный из SQLite (переживает рестарт сервера).

  То же, что уйдёт в модель при следующем ходе (без нового user-сообщения).
  """
    from app.services.llm_context import build_conversation_llm_context

    if await get_accessible_conversation(db, conversation_id, user) is None:
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
            semantic_query=q,
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
    user: RequestUser | None = Depends(get_request_user),
) -> TurnStartedOut:
    """
    Запустить ход агента из внешнего приложения (без WebSocket).

    User-сообщение сохранится в БД; прогресс — GET generation-status и messages.
    При открытом WS клиенте события стрима также уйдут в сокет.
    """
    from app.api.websocket import _schedule_turn_task, _start_background_turn

    if await get_accessible_conversation(db, conversation_id, user) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Беседа не найдена",
        )
    if manager.is_busy(conversation_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Уже выполняется генерация",
        )

    from app.security.trusted_internal import register_integration_urls

    llm_base_url = parse_optional_url(body.llm_base_url)
    sd_webui_url = parse_optional_url(body.sd_webui_url)
    register_integration_urls(llm_base_url, sd_webui_url)
    integration = IntegrationOverrides(
        llm_model=body.model.strip() if body.model else None,
        llm_base_url=llm_base_url,
        sd_webui_url=sd_webui_url,
        macro_context=parse_macro_context_mode(body.macro_context),
        document_rag=bool(body.document_rag),
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
    user: RequestUser | None = Depends(get_request_user),
) -> ConversationOut:
    """Одна беседа по id."""
    conversation = await get_accessible_conversation(db, conversation_id, user)
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
    user: RequestUser | None = Depends(get_request_user),
) -> ConversationOut:
    """Обновить заголовок и/или пресет беседы."""
    conv_repo = ConversationRepository(db)
    conversation = await get_accessible_conversation(db, conversation_id, user)
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


@router.post("/{conversation_id}/generate-title", response_model=ConversationOut)
async def generate_conversation_title_endpoint(
    conversation_id: uuid.UUID,
    body: GenerateTitleCreate | None = None,
    db: AsyncSession = Depends(get_db),
    user: RequestUser | None = Depends(get_request_user),
) -> ConversationOut:
    """Сгенерировать название беседы через LLM по первым сообщениям."""
    from app.integrations.llm_client import LLMClient, LLMError
    from app.services.conversation_title_service import generate_conversation_title

    conversation = await get_accessible_conversation(db, conversation_id, user)
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Беседа не найдена",
        )

    payload = body or GenerateTitleCreate()
    llm_url = parse_optional_url(payload.llm_base_url)
    llm = LLMClient(base_url=llm_url)
    try:
        await generate_conversation_title(
            db,
            conversation_id,
            llm,
            model=payload.model,
        )
        await db.commit()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except LLMError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM недоступен: {exc}",
        ) from exc

    updated = await ConversationRepository(db).get_by_id(conversation_id)
    assert updated is not None
    return ConversationOut.model_validate(updated)


@router.get("/{conversation_id}/export")
async def export_conversation(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: RequestUser | None = Depends(get_request_user),
) -> PlainTextResponse:
    """Скачать беседу как Markdown."""
    conversation = await get_accessible_conversation(db, conversation_id, user)
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Беседа не найдена",
        )
    markdown = await build_conversation_markdown(db, conversation_id)
    if markdown is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Беседа не найдена",
        )
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
    user: RequestUser | None = Depends(get_request_user),
) -> None:
    """Переместить беседу в корзину (окончательное удаление через trash_retention_days)."""
    repo = ConversationRepository(db)
    conversation = await get_accessible_conversation(db, conversation_id, user)
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Беседа не найдена",
        )
    await repo.move_to_trash(conversation)
    await db.commit()
