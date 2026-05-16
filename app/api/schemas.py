"""Схемы запросов и ответов REST API."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


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
