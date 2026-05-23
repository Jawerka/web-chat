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

_MACRO_CONTEXT_SELECTED = "selected"
_MACRO_CONTEXT_FULL = "full"


def parse_macro_context_mode(raw: str | None) -> str:
    """Режим макросов с WS: selected (только @ в тексте) или full (каталог в system)."""
    if raw is not None and str(raw).strip().lower() == _MACRO_CONTEXT_FULL:
        return _MACRO_CONTEXT_FULL
    return _MACRO_CONTEXT_SELECTED


def build_full_macro_catalog_block(
    macros: list[PromptMacro],
    *,
    max_chars: int,
    max_macros: int,
) -> str:
    """
    Ограниченный снимок каталога для LLM (Ф1).

    Не отдаёт всю БД без лимита — обрезка по числу макросов и суммарной длине.
    """
    if not macros or max_macros < 1 or max_chars < 1:
        return ""

    header = (
        "## Каталог быстрых промптов (@alias)\n"
        "Пользователь может ссылаться на @alias в сообщении; ниже полные тексты "
        "доступных макросов. Не выдумывай alias, которых нет в списке.\n"
    )
    parts: list[str] = [header]
    used = len(header)
    included = 0
    truncated_macros = False
    truncated_chars = False

    for macro in macros:
        if included >= max_macros:
            truncated_macros = True
            break
        cat = CATEGORY_LABELS.get(macro.category, str(macro.category))
        label = f" ({macro.label})" if macro.label else ""
        entry_header = f"\n### {cat} · @{macro.alias}{label}\n"
        body = (macro.body or "").strip()
        entry = f"{entry_header}{body}\n"
        if used + len(entry) > max_chars:
            truncated_chars = True
            remain = max_chars - used - len(entry_header) - 20
            if remain > 80:
                parts.append(entry_header)
                parts.append(body[:remain].rstrip())
                parts.append("…\n")
                used = max_chars
            break
        parts.append(entry)
        used += len(entry)
        included += 1

    if truncated_macros or truncated_chars:
        parts.append(
            "\n_(Каталог обрезан по лимиту; для полного списка см. страницу «Быстрые промпты».)_\n",
        )
    return "".join(parts).strip()


def append_full_macro_catalog_to_system(
    system_prompt: str,
    macros: list[PromptMacro],
    *,
    max_chars: int,
    max_macros: int,
) -> str:
    """Добавить снимок каталога к system prompt при macro_context=full."""
    block = build_full_macro_catalog_block(
        macros,
        max_chars=max_chars,
        max_macros=max_macros,
    )
    if not block:
        return system_prompt
    base = (system_prompt or "").strip()
    if base:
        return f"{base}\n\n{block}"
    return block
