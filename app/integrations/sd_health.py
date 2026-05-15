"""
Проверка доступности Stable Diffusion WebUI.
"""

from __future__ import annotations

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


async def check_sd_available() -> str:
    """
    Проверить SD WebUI через GET /sdapi/v1/sd-models.

    Returns:
        "ok" если ответ 2xx, иначе "unavailable".
    """
    url = f"{settings.sd_webui_url.rstrip('/')}/sdapi/v1/sd-models"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            if settings.sd_auth_user and settings.sd_auth_pass:
                response = await client.get(
                    url,
                    auth=(settings.sd_auth_user, settings.sd_auth_pass),
                )
            else:
                response = await client.get(url)
            if response.is_success:
                return "ok"
            logger.warning("SD health: HTTP %s", response.status_code)
    except httpx.HTTPError as exc:
        logger.warning("SD health: %s", exc)
    return "unavailable"
