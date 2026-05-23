"""Публичные настройки для UI (без секретов)."""

from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.config import settings
from app.public_url import (
    public_base_url_lan,
    public_base_url_vpn,
    resolve_public_base_url,
)
from app.integrations.llm_client import LLMClient, LLMError
from app.integrations.runtime_config import parse_optional_url

router = APIRouter(prefix="/config", tags=["config"])


class PublicConfigOut(BaseModel):
    """Лимиты и базовый URL для фронтенда."""

    max_upload_mb: int

    max_files_per_message: int

    public_base_url: str
    public_base_url_lan: str
    public_base_url_vpn: str | None = None
    display_timezone: str

    llm_model: str
    llm_base_url: str
    sd_webui_url: str
    auth_enabled: bool
    rag_enabled: bool


class LlmModelOut(BaseModel):
    """Модель LLM: из .env и фактически используемая (автовыбор)."""

    configured: str

    resolved: str

    source: str


@router.get("", response_model=PublicConfigOut)
async def get_public_config() -> PublicConfigOut:
    """GET /api/config — лимиты загрузки, public_base_url и llm_model из .env."""

    return PublicConfigOut(
        max_upload_mb=settings.max_upload_mb,
        max_files_per_message=settings.max_files_per_message,
        public_base_url=resolve_public_base_url(),
        public_base_url_lan=public_base_url_lan(),
        public_base_url_vpn=public_base_url_vpn(),
        display_timezone=(settings.display_timezone.strip() or "auto"),
        llm_model=settings.llm_model,
        llm_base_url=settings.llm_base_url.rstrip("/"),
        sd_webui_url=settings.sd_webui_url.rstrip("/"),
        auth_enabled=settings.auth_enabled,
        rag_enabled=settings.rag_enabled,
    )


@router.get("/llm-model", response_model=LlmModelOut)
async def get_llm_model(
    llm_base_url: str | None = Query(None, max_length=512),
) -> LlmModelOut:
    """

    GET /api/config/llm-model — модель для отображения в UI.



    resolved — имя, которое сервер отправит в LLM (из .env или GET /v1/models).

    """

    configured = settings.llm_model or ""

    client = LLMClient(base_url=parse_optional_url(llm_base_url))

    try:
        resolved = await client.resolve_model()

    except LLMError:
        resolved = configured or "—"

    source = "config" if configured else "auto"

    return LlmModelOut(configured=configured, resolved=resolved, source=source)
