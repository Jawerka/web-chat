"""Публичные настройки для UI (без секретов)."""

from __future__ import annotations

import asyncio
import time

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
from app.integrations.sd_warmup import (
    invalidate_sd_ready_cache,
    run_sd_warmup_txt2img,
    sd_ready_cached,
)
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


class LlmWarmupIn(BaseModel):
    llm_base_url: str | None = None
    model: str | None = None


class SdWarmupIn(BaseModel):
    title: str | None = None
    sd_webui_url: str | None = None


class SdReadyOut(BaseModel):
    ready: bool
    selected: str = ""
    detail: str = ""


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


async def _fetch_sd_selected_checkpoint(
    client: httpx.AsyncClient,
    base: str,
    auth: tuple[str, str] | None,
) -> str:
    options_res = await client.get(f"{base}/sdapi/v1/options", auth=auth)
    options_res.raise_for_status()
    options = options_res.json() if isinstance(options_res.json(), dict) else {}
    return str(options.get("sd_model_checkpoint") or "").strip()


async def _apply_sd_checkpoint(
    client: httpx.AsyncClient,
    base: str,
    auth: tuple[str, str] | None,
    title: str,
) -> None:
    apply_res = await client.post(
        f"{base}/sdapi/v1/options",
        json={"sd_model_checkpoint": title},
        auth=auth,
    )
    apply_res.raise_for_status()


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
            try:
                await run_sd_warmup_txt2img(client, base, auth, checkpoint=title)
            except RuntimeError as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=str(exc),
                ) from exc

    return {"ok": True, "selected": title, "warmup": bool(body.warmup)}


@router.get("/sd-ready", response_model=SdReadyOut)
async def get_sd_ready(
    sd_webui_url: str | None = Query(None, max_length=512),
    probe: bool = Query(False, description="Проверить tiny txt2img, не только кэш"),
) -> SdReadyOut:
    """
    Готов ли SD checkpoint в VRAM.

    /sd-models может отвечать OK при выгруженной модели; probe=true делает реальный прогрев.
    """
    base = (parse_optional_url(sd_webui_url) or settings.sd_webui_url).rstrip("/")
    auth = _sd_auth()
    timeout = max(20.0, float(settings.request_timeout))

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            selected = await _fetch_sd_selected_checkpoint(client, base, auth)
        except httpx.HTTPError as exc:
            return SdReadyOut(
                ready=False,
                detail=f"SD API недоступен: {exc}",
            )

        if not probe and selected and sd_ready_cached(base, selected):
            return SdReadyOut(ready=True, selected=selected, detail="Кэш: модель прогрета")

        if not probe:
            return SdReadyOut(
                ready=False,
                selected=selected,
                detail="Требуется прогрев checkpoint",
            )

        try:
            await run_sd_warmup_txt2img(client, base, auth, checkpoint=selected)
        except RuntimeError as exc:
            return SdReadyOut(ready=False, selected=selected, detail=str(exc))

    return SdReadyOut(ready=True, selected=selected, detail="Прогрев успешен")


@router.post("/sd-warmup")
async def warmup_sd(body: SdWarmupIn) -> dict:
    """Применить checkpoint и обязательно прогреть SD (tiny txt2img)."""
    base = (parse_optional_url(body.sd_webui_url) or settings.sd_webui_url).rstrip("/")
    auth = _sd_auth()
    timeout = max(30.0, float(settings.request_timeout))

    async with httpx.AsyncClient(timeout=timeout) as client:
        title = (body.title or "").strip()
        if not title:
            try:
                title = await _fetch_sd_selected_checkpoint(client, base, auth)
            except httpx.HTTPError as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Не удалось получить SD checkpoint: {exc}",
                ) from exc
        if not title:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Не задан SD checkpoint",
            )

        try:
            await _apply_sd_checkpoint(client, base, auth, title)
            await run_sd_warmup_txt2img(client, base, auth, checkpoint=title)
        except httpx.HTTPError as exc:
            invalidate_sd_ready_cache(base)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Не удалось применить SD модель: {exc}",
            ) from exc
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=str(exc),
            ) from exc

    return {"ok": True, "selected": title}


@router.post("/llm-warmup")
async def warmup_llm(body: LlmWarmupIn) -> dict:
    """
    Прогрев LLM: короткий запрос без tools загружает модель в память.

    Повторяет при HTTP 503 (Loading model) до llm_model_load_wait_sec.
    """
    import logging

    log = logging.getLogger(__name__)
    parsed_llm = parse_optional_url(body.llm_base_url)
    register_integration_urls(parsed_llm, None)
    client = LLMClient(base_url=parsed_llm)

    try:
        model = await client.resolve_model(body.model)
    except LLMError as exc:
        log.warning("llm-warmup: resolve_model failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Не удалось выбрать модель LLM: {exc}",
        ) from exc

    log.info("llm-warmup: прогрев модели %s (url=%s)", model, parsed_llm or settings.llm_base_url)
    deadline = time.monotonic() + max(5.0, float(settings.llm_model_load_wait_sec))
    last_exc: LLMError | None = None
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        try:
            await client.complete_plain_text(
                [{"role": "user", "content": "."}],
                model=body.model or model,
                max_tokens=1,
                temperature=0,
                disable_thinking=True,
                allow_reasoning_fallback=False,
            )
            log.info("llm-warmup: модель %s готова (попытка %d)", model, attempt)
            return {"ok": True, "model": model}
        except LLMError as exc:
            last_exc = exc
            msg = str(exc).lower()
            if "503" in msg or "loading" in msg:
                log.info(
                    "llm-warmup: модель загружается, повтор через %.1f с (попытка %d): %s",
                    settings.llm_model_load_retry_sec,
                    attempt,
                    exc,
                )
                await asyncio.sleep(settings.llm_model_load_retry_sec)
                continue
            log.warning("llm-warmup: ошибка (попытка %d): %s", attempt, exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=str(exc),
            ) from exc

    log.warning("llm-warmup: таймаут для %s после %d попыток: %s", model, attempt, last_exc)
    raise HTTPException(
        status_code=status.HTTP_504_GATEWAY_TIMEOUT,
        detail=str(last_exc) or "Таймаут прогрева LLM",
    )
