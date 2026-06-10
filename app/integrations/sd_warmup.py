"""
Прогрев SD WebUI: загрузка checkpoint в VRAM через tiny txt2img.

После автовыгрузки моделей API /sd-models отвечает OK, но txt2img падает с 500
(cuda/cpu mismatch). Прогрев обязателен и не должен игнорировать ошибки.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# base_url → (ready, expires_monotonic, checkpoint_title)
_ready_cache: dict[str, tuple[bool, float, str]] = {}


def warmup_txt2img_payload() -> dict[str, Any]:
    return {
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


def mark_sd_ready(sd_base: str, checkpoint: str, *, ttl_sec: float = 300.0) -> None:
    key = sd_base.rstrip("/")
    _ready_cache[key] = (True, time.monotonic() + max(30.0, ttl_sec), checkpoint)


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


async def reload_sd_checkpoint(
    client: httpx.AsyncClient,
    base: str,
    auth: tuple[str, str] | None,
) -> None:
    """POST /sdapi/v1/reload-checkpoint — помогает после частичной выгрузки."""
    try:
        resp = await client.post(f"{base.rstrip('/')}/sdapi/v1/reload-checkpoint", auth=auth)
        resp.raise_for_status()
        logger.info("SD reload-checkpoint OK")
    except httpx.HTTPError as exc:
        logger.warning("SD reload-checkpoint: %s", exc)


async def run_sd_warmup_txt2img(
    client: httpx.AsyncClient,
    base: str,
    auth: tuple[str, str] | None,
    *,
    checkpoint: str = "",
    try_reload: bool = True,
) -> None:
    """
    Прогрев через txt2img. При ошибке — invalidate cache и RuntimeError с текстом SD.

    Raises:
        RuntimeError: SD не готов (500, сеть и т.д.).
    """
    url = f"{base.rstrip('/')}/sdapi/v1/txt2img"
    timeout = max(30.0, float(settings.request_timeout))
    payload = warmup_txt2img_payload()

    async def _post() -> httpx.Response:
        return await client.post(url, json=payload, auth=auth, timeout=timeout)

    resp = await _post()
    if resp.is_success:
        data = resp.json()
        if data.get("images"):
            if checkpoint:
                mark_sd_ready(base, checkpoint)
            logger.info("SD warmup txt2img OK (checkpoint=%s)", checkpoint or "—")
            return

    detail = parse_sd_error_body(resp)
    if try_reload and resp.status_code >= 500:
        await reload_sd_checkpoint(client, base, auth)
        resp = await _post()
        if resp.is_success:
            data = resp.json()
            if data.get("images"):
                if checkpoint:
                    mark_sd_ready(base, checkpoint)
                logger.info("SD warmup OK после reload-checkpoint")
                return
        detail = parse_sd_error_body(resp)

    invalidate_sd_ready_cache(base)
    hint = (
        " Перезапустите SD WebUI или переключите checkpoint вручную."
        if "cuda" in detail.lower() or "cpu" in detail.lower()
        else ""
    )
    raise RuntimeError(f"SD не готов: {detail}.{hint}")
