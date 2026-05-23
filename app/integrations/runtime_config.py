"""Переопределения адресов LLM/SD из WebSocket (настройки браузера)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.config import settings
from app.services.prompt_macro_service import parse_macro_context_mode

_MAX_URL_LEN = 512


@dataclass(frozen=True)
class IntegrationOverrides:
    """Опциональные URL и модель с клиента."""

    llm_model: str | None = None
    llm_base_url: str | None = None
    sd_webui_url: str | None = None
    macro_context: str = "selected"
    document_rag: bool = False


def resolve_llm_base_url(override: str | None = None) -> str:
    """Базовый URL LLM API: override или .env."""
    if override and override.strip():
        return override.strip().rstrip("/")
    return settings.llm_base_url.rstrip("/")


def resolve_sd_webui_url(override: str | None = None) -> str:
    """URL Stable Diffusion WebUI: override или .env."""
    if override and override.strip():
        return override.strip().rstrip("/")
    return settings.sd_webui_url.rstrip("/")


def parse_optional_url(raw: Any) -> str | None:
    """Нормализовать URL из WS; пустое/некорректное → None."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or len(text) > _MAX_URL_LEN:
        return None
    if not text.startswith(("http://", "https://")):
        return None
    return text.rstrip("/")


def parse_integration_overrides(data: dict[str, Any]) -> IntegrationOverrides:
    """Собрать переопределения из тела WS-сообщения."""
    raw_model = data.get("model")
    llm_model = None
    if raw_model is not None:
        text = str(raw_model).strip()
        llm_model = text or None
    raw_rag = data.get("document_rag")
    document_rag = raw_rag is True or raw_rag in (1, "1", "true", "True")
    return IntegrationOverrides(
        llm_model=llm_model,
        llm_base_url=parse_optional_url(data.get("llm_base_url")),
        sd_webui_url=parse_optional_url(data.get("sd_webui_url")),
        macro_context=parse_macro_context_mode(data.get("macro_context")),
        document_rag=document_rag,
    )
