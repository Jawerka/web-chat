"""Схемы запросов и ответов REST API."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.db.models import PromptMacroCategory


class ConversationCreate(BaseModel):
    """Тело запроса на создание беседы."""

    title: str | None = Field(None, max_length=200)
    preset_id: UUID | None = Field(
        None,
        description="Если не указан — используется пресет с is_default=true",
    )


class ConversationUpdate(BaseModel):
    """Тело PATCH для беседы."""

    title: str | None = Field(None, max_length=200)
    preset_id: UUID | None = None


class ConversationOut(BaseModel):
    """Беседа в ответе API."""

    id: UUID
    title: str
    preset_id: UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PresetOut(BaseModel):
    """Пресет в ответе API."""

    id: UUID
    name: str
    slug: str
    system_prompt: str
    is_default: bool
    sort_order: int
    created_at: datetime

    model_config = {"from_attributes": True}


class PresetUpdate(BaseModel):
    """PATCH тела пресета."""

    system_prompt: str = Field(..., max_length=200_000)


class AttachmentOut(BaseModel):
    """Вложение в ответе upload API."""

    id: UUID
    original_name: str
    mime_type: str
    size_bytes: int
    preview_url: str | None = None


class UploadResponse(BaseModel):
    """Ответ POST /api/upload."""

    attachments: list[AttachmentOut]


class PromptMacroOut(BaseModel):
    """Быстрый промпт (@alias)."""

    id: UUID
    category: str
    category_label: str
    alias: str
    label: str | None
    body: str
    sort_order: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PromptMacroCreate(BaseModel):
    category: PromptMacroCategory = PromptMacroCategory.CHARACTER
    alias: str = Field(..., min_length=1, max_length=64)
    label: str | None = Field(None, max_length=120)
    body: str = Field(..., min_length=1, max_length=50_000)
    sort_order: int = 0


class PromptMacroUpdate(BaseModel):
    category: PromptMacroCategory | None = None
    alias: str | None = Field(None, min_length=1, max_length=64)
    label: str | None = Field(None, max_length=120)
    body: str | None = Field(None, min_length=1, max_length=50_000)
    sort_order: int | None = None


class MessageSearchHit(BaseModel):
    """Одно совпадение в поиске по истории."""

    message_id: UUID | None = None
    conversation_id: UUID
    conversation_title: str
    role: str
    snippet: str
    created_at: datetime
    match_kind: str = "message"


class MessageOut(BaseModel):
    """Сообщение в истории чата."""

    id: UUID
    conversation_id: UUID
    role: str
    content_text: str | None = None
    content_json: dict | None = None
    created_at: datetime


class MessageUpdate(BaseModel):
    """PATCH тела сообщения."""

    content_text: str = Field(..., min_length=1, max_length=100_000)
