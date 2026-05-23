"""
Клиент embeddings (OpenAI-compatible, LAN), для поиска по @alias (Ф2).
"""

from __future__ import annotations

import logging
import math

from openai import AsyncOpenAI

from app.config import settings
from app.integrations.runtime_config import resolve_llm_base_url

logger = logging.getLogger(__name__)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Косинусное сходство; 0 при нулевой норме."""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class EmbeddingClient:
    """Async embeddings через тот же LLM gateway, что и chat completions."""

    def __init__(self, *, base_url: str | None = None) -> None:
        self._base_url = resolve_llm_base_url(base_url)
        self._client = AsyncOpenAI(
            base_url=self._base_url,
            api_key=settings.llm_api_key or "not-needed",
            timeout=min(60, settings.llm_timeout_sec),
            max_retries=0,
        )

    async def embed_text(self, text: str, *, model: str | None = None) -> list[float] | None:
        """Вектор для текста; None если embeddings не настроены или ошибка."""
        model_name = (model or settings.embedding_model or "").strip()
        if not model_name:
            return None
        chunk = text.strip()
        if not chunk:
            return None
        try:
            response = await self._client.embeddings.create(
                model=model_name,
                input=chunk[:8000],
            )
            data = response.data
            if not data:
                return None
            return list(data[0].embedding)
        except Exception as exc:
            logger.warning("embeddings недоступны (%s): %s", model_name, exc)
            return None
