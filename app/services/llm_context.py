"""
Сборка контекста беседы для LLM (история из SQLite, переживает перезапуск сервера).
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.repositories import (
    ConversationRepository,
    MessageRepository,
    PresetRepository,
    PromptMacroRepository,
)
from app.integrations.tool_definitions import tools_for_preset_slug
from app.services.macro_search_service import apply_macro_context_to_system
from app.services.message_builder import (
    history_to_llm_messages,
    sanitize_llm_messages_for_vision,
)
from app.services.prompt_macro_service import (
    alias_map_from_macros,
    parse_macro_context_mode,
)

logger = logging.getLogger(__name__)


async def build_conversation_llm_context(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    *,
    macro_context: str | None = None,
    max_messages: int | None = None,
    semantic_query: str | None = None,
) -> dict[str, Any]:
    """
    Восстановить контекст, который уйдёт в LLM при следующем ходе.

    История читается из БД (не из памяти процесса) — после рестарта сервера
    контекст собирается заново из сохранённых сообщений.
    """
    conv_repo = ConversationRepository(session)
    preset_repo = PresetRepository(session)
    msg_repo = MessageRepository(session)
    macro_repo = PromptMacroRepository(session)

    conversation = await conv_repo.get_by_id(conversation_id)
    if conversation is None:
        raise ValueError("Беседа не найдена")

    await msg_repo.settle_stale_streaming_assistant_messages(conversation_id)

    preset = await preset_repo.get_by_id(conversation.preset_id)
    system_prompt = preset.system_prompt if preset else ""
    mode = parse_macro_context_mode(macro_context)

    all_macros = await macro_repo.list_all()
    alias_to_body = alias_map_from_macros(all_macros)
    system_prompt = await apply_macro_context_to_system(
        session,
        system_prompt,
        mode,
        user_text=semantic_query or "",
        all_macros=all_macros,
    )

    cap = max_messages or settings.max_history_messages
    t0 = time.perf_counter()
    history = await msg_repo.list_for_llm(conversation_id, cap)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    if elapsed_ms > 300:
        logger.warning(
            "Сборка истории LLM заняла %.0f ms (conversation=%s, messages=%d, cap=%d). "
            "При лагах уменьшите MAX_HISTORY_MESSAGES.",
            elapsed_ms,
            conversation_id,
            len(history),
            cap,
        )

    llm_messages: list[dict[str, Any]] = []
    if system_prompt:
        llm_messages.append({"role": "system", "content": system_prompt})
    llm_messages.extend(history_to_llm_messages(history, alias_to_body=alias_to_body))
    llm_messages = await sanitize_llm_messages_for_vision(session, llm_messages)

    return {
        "conversation_id": str(conversation_id),
        "preset_slug": preset.slug if preset else None,
        "macro_context": mode,
        "max_history_messages": cap,
        "messages_in_context": len(history),
        "truncated": len(history) >= cap,
        "tools_available": bool(tools_for_preset_slug(preset.slug if preset else None)),
        "messages": llm_messages,
    }
