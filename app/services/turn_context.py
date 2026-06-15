"""Состояние хода агента (P3.2): общие поля tool-loop и стриминга."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.services.conversation_tool_state import ConversationToolState
from app.services.streaming_draft import AssistantStreamDraft

EventEmitter = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass
class TurnContext:
    """
    Мутабельное состояние хода между раундами LLM/tools.

    Создаётся один раз после подготовки llm_messages; передаётся в
    `_run_completion_tool_calls` и финализацию assistant.
    """

    conversation_id: uuid.UUID
    user_message_id: uuid.UUID
    emit: EventEmitter
    cancel_event: asyncio.Event
    llm_model: str | None
    llm_messages: list[dict[str, Any]]
    rag_sources: list[dict[str, Any]] | None = None
    all_image_urls: list[str] = field(default_factory=list)
    all_image_asset_ids: list[str] = field(default_factory=list)
    media_url_rewrites: dict[str, str] = field(default_factory=dict)
    tool_calls_meta: list[dict[str, Any]] = field(default_factory=list)
    tool_state: ConversationToolState = field(default_factory=ConversationToolState)
    consecutive_tool_skips: int = 0
    stream_draft: AssistantStreamDraft | None = None

    def __post_init__(self) -> None:
        if self.stream_draft is None:
            self.stream_draft = AssistantStreamDraft(self.conversation_id, self.emit)

    @property
    def existing_message_id(self) -> uuid.UUID | None:
        draft = self.stream_draft
        return draft.message_id if draft is not None else None
