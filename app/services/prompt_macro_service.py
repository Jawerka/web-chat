"""Быстрые промпты (@alias) — развёртка для LLM и валидация."""

from __future__ import annotations

import re
from typing import Any

from app.db.models import PromptMacro, PromptMacroCategory

# @@alias — экранирование: оба @ не попадают в текст для LLM
_MACRO_MENTION_RE = re.compile(r"@?@([a-zA-Z0-9_-]+)")
_ALIAS_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")


def normalize_alias(value: str) -> str:
    """Нормализовать alias: lowercase, без @."""
    raw = value.strip().lstrip("@").lower().replace(" ", "_")
    return raw


def validate_alias(value: str) -> str:
    """Проверить alias; вернуть нормализованное значение или ValueError."""
    alias = normalize_alias(value)
    if not alias or not _ALIAS_RE.match(alias):
        raise ValueError(
            "Alias: латиница, цифры, _ и - (2–63 символа), например rainbow_dash",
        )
    return alias


def alias_map_from_macros(macros: list[PromptMacro]) -> dict[str, str]:
    """alias.lower() → body."""
    return {m.alias.lower(): m.body for m in macros}


def expand_macro_text(text: str, alias_to_body: dict[str, str]) -> str:
    """Заменить @alias на полный промпт (неизвестные @ остаются как есть)."""
    if not text or not alias_to_body:
        return text

    def repl(match: re.Match[str]) -> str:
        key = match.group(1).lower()
        return alias_to_body.get(key, match.group(0))

    return _MACRO_MENTION_RE.sub(repl, text)


def expand_parts_for_llm(
    parts: list[dict[str, Any]],
    alias_to_body: dict[str, str],
) -> list[dict[str, Any]]:
    """Развернуть текстовые parts для LLM."""
    from copy import deepcopy

    out = deepcopy(parts)
    for part in out:
        if part.get("type") == "text" and part.get("text"):
            part["text"] = expand_macro_text(str(part["text"]), alias_to_body)
    return out


CATEGORY_LABELS: dict[PromptMacroCategory, str] = {
    PromptMacroCategory.CHARACTER: "Персонажи",
    PromptMacroCategory.ENVIRONMENT: "Окружение",
    PromptMacroCategory.SITUATION: "Ситуации",
    PromptMacroCategory.OTHER: "Прочее",
}
