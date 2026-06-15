"""
Прогрев SD WebUI: загрузка checkpoint в VRAM без лишней генерации.

После автовыгрузки API /sd-models может отвечать OK, но img2img падает с 500
(cuda/cpu mismatch). Прогрев — select checkpoint + reload-checkpoint.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Совпадает с окном «недавней активности» на клиенте (40 мин).
READY_CACHE_TTL_SEC = 40 * 60

# base_url → (ready, expires_monotonic, checkpoint_title)
_ready_cache: dict[str, tuple[bool, float, str]] = {}


def parse_sd_error_body(response: httpx.Response) -> str:
    try:
        data = response.json()
        if isinstance(data, dict):
            for key in ("errors", "detail", "error", "message"):
                val = data.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
    except Exception:
        pass
    text = (response.text or "").strip()
    return text[:300] if text else f"HTTP {response.status_code}"


def invalidate_sd_ready_cache(sd_base: str | None = None) -> None:
    """Сбросить кэш готовности (после 500 или явной выгрузки)."""
    if sd_base is None:
        _ready_cache.clear()
        return
    key = sd_base.rstrip("/")
    _ready_cache.pop(key, None)


def mark_sd_ready(
    sd_base: str,
    checkpoint: str,
    *,
    ttl_sec: float = READY_CACHE_TTL_SEC,
) -> None:
    key = sd_base.rstrip("/")
    _ready_cache[key] = (True, time.monotonic() + max(60.0, ttl_sec), checkpoint)


def sd_ready_cached(sd_base: str, checkpoint: str) -> bool:
    key = sd_base.rstrip("/")
    entry = _ready_cache.get(key)
    if entry is None:
        return False
    ready, expires, cached_ckpt = entry
    if not ready or time.monotonic() >= expires:
        _ready_cache.pop(key, None)
        return False
    return cached_ckpt == checkpoint


async def fetch_sd_selected_checkpoint(
    client: httpx.AsyncClient,
    base: str,
    auth: tuple[str, str] | None,
) -> str:
    resp = await client.get(f"{base.rstrip('/')}/sdapi/v1/options", auth=auth)
    resp.raise_for_status()
    options = resp.json() if isinstance(resp.json(), dict) else {}
    return str(options.get("sd_model_checkpoint") or "").strip()


async def apply_sd_checkpoint(
    client: httpx.AsyncClient,
    base: str,
    auth: tuple[str, str] | None,
    title: str,
) -> None:
    resp = await client.post(
        f"{base.rstrip('/')}/sdapi/v1/options",
        json={"sd_model_checkpoint": title},
        auth=auth,
    )
    resp.raise_for_status()


async def reload_sd_checkpoint(
    client: httpx.AsyncClient,
    base: str,
    auth: tuple[str, str] | None,
) -> None:
    """POST /sdapi/v1/reload-checkpoint — загрузка выбранного checkpoint в VRAM."""
    resp = await client.post(
        f"{base.rstrip('/')}/sdapi/v1/reload-checkpoint",
        auth=auth,
        timeout=max(120.0, float(settings.request_timeout)),
    )
    resp.raise_for_status()
    logger.info("SD reload-checkpoint OK")


async def run_sd_warmup_load(
    client: httpx.AsyncClient,
    base: str,
    auth: tuple[str, str] | None,
    *,
    checkpoint: str = "",
    apply_checkpoint: bool = True,
) -> None:
    """
    Загрузить checkpoint в VRAM (options + reload-checkpoint), без txt2img.

    Raises:
        RuntimeError: SD не готов (сеть, HTTP 5xx).
    """
    base = base.rstrip("/")
    title = checkpoint.strip()
    try:
        if apply_checkpoint and title:
            await apply_sd_checkpoint(client, base, auth, title)
        await reload_sd_checkpoint(client, base, auth)
        if not title:
            title = await fetch_sd_selected_checkpoint(client, base, auth)
        if title:
            mark_sd_ready(base, title)
        logger.info("SD checkpoint загружен: %s", title or "—")
    except httpx.HTTPError as exc:
        invalidate_sd_ready_cache(base)
        detail = str(exc)
        if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
            detail = parse_sd_error_body(exc.response)
        hint = (
            " Перезапустите SD WebUI или переключите checkpoint вручную."
            if "cuda" in detail.lower() or "cpu" in detail.lower()
            else ""
        )
        raise RuntimeError(f"SD не готов: {detail}.{hint}") from exc


# Совместимость: старое имя API.
run_sd_warmup_txt2img = run_sd_warmup_load
