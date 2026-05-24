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
from app.security.trusted_internal import (
    register_integration_urls,
    trusted_internal_hosts_summary,
)

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
    trash_retention_days: int = 3
    trusted_internal_env_hosts: list[str] = []
    trusted_internal_ui_hosts: list[str] = []
    trusted_internal_ip_count: int = 0


class TrustedInternalSyncIn(BaseModel):
    llm_base_url: str | None = None
    sd_webui_url: str | None = None


class TrustedInternalSyncOut(BaseModel):
    env_hosts: list[str]
    ui_hosts: list[str]
    ip_count: int


class LlmModelOut(BaseModel):
    """Модель LLM: из .env и фактически используемая (автовыбор)."""

    configured: str

    resolved: str

    source: str


@router.get("", response_model=PublicConfigOut)
async def get_public_config() -> PublicConfigOut:
    """GET /api/config — лимиты загрузки, public_base_url и llm_model из .env."""

    ti = trusted_internal_hosts_summary()
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
        trash_retention_days=max(1, settings.trash_retention_days),
        trusted_internal_env_hosts=ti["env_hosts"],
        trusted_internal_ui_hosts=ti["ui_hosts"],
        trusted_internal_ip_count=len(ti["ips"]),
    )


@router.post("/trusted-internal/sync", response_model=TrustedInternalSyncOut)
async def sync_trusted_internal(body: TrustedInternalSyncIn) -> TrustedInternalSyncOut:
    """
    Зарегистрировать хосты LLM/SD из настроек UI (localStorage).

    Вызывается при сохранении адресов в настройках чата.
    """
    llm = parse_optional_url(body.llm_base_url)
    sd = parse_optional_url(body.sd_webui_url)
    register_integration_urls(llm, sd)
    ti = trusted_internal_hosts_summary()
    return TrustedInternalSyncOut(
        env_hosts=ti["env_hosts"],
        ui_hosts=ti["ui_hosts"],
        ip_count=len(ti["ips"]),
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

    parsed_llm = parse_optional_url(llm_base_url)
    register_integration_urls(parsed_llm, None)
    client = LLMClient(base_url=parsed_llm)

    try:
        resolved = await client.resolve_model()

    except LLMError:
        resolved = configured or "—"

    source = "config" if configured else "auto"

    return LlmModelOut(configured=configured, resolved=resolved, source=source)
