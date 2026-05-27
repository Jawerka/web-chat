"""Публичные настройки для UI (без секретов)."""

from __future__ import annotations

import asyncio

import httpx
from fastapi import APIRouter, HTTPException, Query, status
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


class LlmModelsOut(BaseModel):
    """Список доступных моделей и текущий выбор."""

    configured: str
    resolved: str
    source: str
    models: list[str]


class SdModelEntry(BaseModel):
    title: str
    model_name: str = ""
    hash: str = ""
    sha256: str = ""
    filename: str = ""
    config: str = ""


class SdModelsOut(BaseModel):
    models: list[SdModelEntry]
    selected: str = ""


class SdModelSelectIn(BaseModel):
    title: str
    sd_webui_url: str | None = None
    warmup: bool = True


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


@router.get("/llm-models", response_model=LlmModelsOut)
async def get_llm_models(
    llm_base_url: str | None = Query(None, max_length=512),
) -> LlmModelsOut:
    """
    GET /api/config/llm-models — список моделей с указанного API.

    Возвращает:
    - models: список id из GET /v1/models (может быть пустым при ошибке);
    - configured/resolved/source: текущее состояние выбора на сервере.
    """
    configured = settings.llm_model or ""
    parsed_llm = parse_optional_url(llm_base_url)
    register_integration_urls(parsed_llm, None)
    client = LLMClient(base_url=parsed_llm)

    models: list[str] = []
    try:
        models = await client.fetch_models()
    except LLMError:
        models = []

    try:
        resolved = await client.resolve_model()
    except LLMError:
        resolved = configured or (models[0] if models else "—")

    source = "config" if configured else "auto"
    return LlmModelsOut(
        configured=configured,
        resolved=resolved,
        source=source,
        models=models,
    )


def _sd_auth() -> tuple[str, str] | None:
    if settings.sd_auth_user and settings.sd_auth_pass:
        return (settings.sd_auth_user, settings.sd_auth_pass)
    return None


@router.get("/sd-models", response_model=SdModelsOut)
async def get_sd_models(
    sd_webui_url: str | None = Query(None, max_length=512),
) -> SdModelsOut:
    """GET /api/config/sd-models — список SD checkpoint'ов и текущий выбранный."""
    base = (parse_optional_url(sd_webui_url) or settings.sd_webui_url).rstrip("/")
    auth = _sd_auth()

    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            models_res = await client.get(f"{base}/sdapi/v1/sd-models", auth=auth)
            models_res.raise_for_status()
            options_res = await client.get(f"{base}/sdapi/v1/options", auth=auth)
            options_res.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Не удалось получить модели SD: {exc}",
            ) from exc

    raw_models = models_res.json() if isinstance(models_res.json(), list) else []
    items: list[SdModelEntry] = []
    for item in raw_models:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        items.append(
            SdModelEntry(
                title=title,
                model_name=str(item.get("model_name") or ""),
                hash=str(item.get("hash") or ""),
                sha256=str(item.get("sha256") or ""),
                filename=str(item.get("filename") or ""),
                config=str(item.get("config") or ""),
            )
        )

    options = options_res.json() if isinstance(options_res.json(), dict) else {}
    selected = str(options.get("sd_model_checkpoint") or "").strip()
    return SdModelsOut(models=items, selected=selected)


@router.post("/sd-models/select")
async def set_sd_model(body: SdModelSelectIn) -> dict:
    """
    Применить SD checkpoint на стороне WebUI.

    Дополнительно можно сделать мягкий прогрев (tiny txt2img), чтобы модель точно загрузилась
    до следующей пользовательской генерации.
    """
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Пустое имя модели")
    base = (parse_optional_url(body.sd_webui_url) or settings.sd_webui_url).rstrip("/")
    auth = _sd_auth()

    async with httpx.AsyncClient(timeout=max(30.0, float(settings.request_timeout))) as client:
        try:
            apply_res = await client.post(
                f"{base}/sdapi/v1/options",
                json={"sd_model_checkpoint": title},
                auth=auth,
            )
            apply_res.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Не удалось применить SD модель: {exc}",
            ) from exc

        if body.warmup:
            # "Фейковый" прогрев, чтобы WebUI успел подгрузить веса до реальной генерации.
            payload = {
                "prompt": "warmup",
                "negative_prompt": "",
                "steps": 1,
                "width": 64,
                "height": 64,
                "cfg_scale": 1,
                "sampler_name": settings.sd_sampler or "Euler a",
                "seed": -1,
                "n_iter": 1,
                "batch_size": 1,
                "do_not_save_samples": True,
                "do_not_save_grid": True,
            }
            try:
                # Ждём в фоне пула, чтобы не блокировать event loop при долгой загрузке.
                await asyncio.to_thread(
                    lambda: httpx.post(
                        f"{base}/sdapi/v1/txt2img",
                        json=payload,
                        auth=auth,
                        timeout=max(30.0, float(settings.request_timeout)),
                    )
                )
            except Exception:
                # Прогрев необязателен: модель уже применена через /options.
                pass

    return {"ok": True, "selected": title, "warmup": bool(body.warmup)}
