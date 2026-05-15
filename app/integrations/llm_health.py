"""
Проверка доступности LLM (OpenAI-compatible).
"""

from __future__ import annotations

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


async def check_llm_available() -> str:
    """
    Проверить LLM через GET /v1/models.

    Returns:
        "ok" или "unavailable".
    """
    url = f"{settings.llm_base_url.rstrip('/')}/models"
    headers = {}
    if settings.llm_api_key:
        headers["Authorization"] = f"Bearer {settings.llm_api_key}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, headers=headers or None)
            if response.is_success:
                return "ok"
            logger.warning("LLM health: HTTP %s", response.status_code)
    except httpx.HTTPError as exc:
        logger.warning("LLM health: %s", exc)
    return "unavailable"
