"""Схемы запросов и ответов REST API."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_serializer

from app.datetime_utils import datetime_to_utc_iso
from app.db.models import PromptMacroCategory


class ConversationCreate(BaseModel):
    """Тело запроса на создание беседы."""

    title: str | None = Field(None, max_length=200)
    text: str | None = Field(
        None,
        max_length=100_000,
        description="Черновик composer (теги, prompt) без отправки агенту",
    )
    preset_slug: str | None = Field(
        None,
        max_length=64,
        description="Slug пресета; альтернатива preset_id",
    )
    preset_id: UUID | None = Field(
        None,
        description="Если не указан — используется пресет с is_default=true",
    )


class ConversationUpdate(BaseModel):
    """Тело PATCH для беседы."""

    title: str | None = Field(None, max_length=200)
    preset_id: UUID | None = None


class GenerateTitleCreate(BaseModel):
    """Ручная генерация названия беседы через LLM."""

    model: str | None = Field(None, description="Переопределение модели LLM")
    llm_base_url: str | None = Field(None, description="Базовый URL LLM API из настроек чата")


class ConversationOut(BaseModel):
    """Беседа в ответе API."""

    id: UUID
    title: str
    preset_id: UUID
    created_at: datetime
    updated_at: datetime
    in_progress: bool = False
    deleted_at: datetime | None = None

    model_config = {"from_attributes": True}

    @field_serializer("created_at", "updated_at", "deleted_at")
    @classmethod
    def _serialize_utc(cls, dt: datetime | None) -> str | None:
        if dt is None:
            return None
        return datetime_to_utc_iso(dt)


class ConversationDetailOut(ConversationOut):
    """Беседа с серверным черновиком composer (GET по id)."""

    message_count: int = 0
    composer_text: str = ""
    pending_attachments: list[AttachmentOut] = Field(default_factory=list)


class ConversationCreatedOut(ConversationOut):
    """Ответ POST /api/conversations — handoff для внешних клиентов."""

    conversation_id: UUID
    composer_text: str = ""
    chat_url: str
    attachments: list[AttachmentOut] = Field(default_factory=list)


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

    @field_serializer("created_at")
    @classmethod
    def _serialize_utc(cls, dt: datetime) -> str:
        return datetime_to_utc_iso(dt)


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

    @field_serializer("created_at", "updated_at")
    @classmethod
    def _serialize_utc(cls, dt: datetime) -> str:
        return datetime_to_utc_iso(dt)


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


class PromptMacroSearchHit(BaseModel):
    """Результат semantic/keyword поиска по каталогу @alias (Ф2)."""

    id: UUID
    alias: str
    label: str | None
    category: str
    score: float
    match: str


class PromptMacroReindexOut(BaseModel):
    updated: int
    skipped: int
    total: int


class MessageSearchHit(BaseModel):
    """Одно совпадение в поиске по истории."""

    message_id: UUID | None = None
    conversation_id: UUID
    conversation_title: str
    role: str
    snippet: str
    created_at: datetime
    match_kind: str = "message"

    @field_serializer("created_at")
    @classmethod
    def _serialize_utc(cls, dt: datetime) -> str:
        return datetime_to_utc_iso(dt)


class MessageOut(BaseModel):
    """Сообщение в истории чата."""

    id: UUID
    conversation_id: UUID
    role: str
    content_text: str | None = None
    content_json: dict | None = None
    created_at: datetime

    @field_serializer("created_at")
    @classmethod
    def _serialize_utc(cls, dt: datetime) -> str:
        return datetime_to_utc_iso(dt)


class MessageUpdate(BaseModel):
    """PATCH тела сообщения."""

    content_text: str = Field(..., min_length=1, max_length=100_000)
    attachment_ids: list[UUID] | None = Field(
        None,
        description="Только для user: полный список id вложений сообщения после редактирования",
    )


class TurnCreate(BaseModel):
    """Запуск хода агента через REST (внешние приложения, без WebSocket)."""

    text: str = Field(..., min_length=1, max_length=100_000)
    attachment_ids: list[UUID] = Field(default_factory=list)
    macro_context: str | None = Field(
        None,
        description="selected | full | semantic — контекст @alias для LLM",
    )
    document_rag: bool | None = Field(
        None,
        description="Подмешать top-K фрагментов документов беседы в system prompt",
    )
    wd_tagger: bool | None = Field(
        None,
        description="Распознать WD14-теги для прикреплённых изображений",
    )
    llm_base_url: str | None = Field(None, max_length=512)
    sd_webui_url: str | None = Field(None, max_length=512)
    model: str | None = Field(None, max_length=200)


class TurnStartedOut(BaseModel):
    """Ответ POST .../turn — ход в фоне, статус через generation-status и messages."""

    status: str = "started"
    conversation_id: UUID


class ImageSourceIn(BaseModel):
    """Источник изображения для POST /api/conversations/from-image (JSON)."""

    asset_id: UUID | None = None
    disk_filename: str | None = Field(None, max_length=255)
    url: str | None = Field(None, max_length=2048)


class ConversationFromImageCreate(BaseModel):
    """JSON-тело POST /api/conversations/from-image."""

    text: str | None = Field(None, max_length=100_000)
    title: str | None = Field(None, max_length=200)
    preset_slug: str | None = Field("img2img", max_length=64)
    image: ImageSourceIn


class ConversationFromImageOut(BaseModel):
    """Ответ POST /api/conversations/from-image."""

    conversation_id: UUID
    title: str
    preset_id: UUID
    attachments: list[AttachmentOut]
    composer_text: str
    chat_url: str
