"""Фрагмент текста вокруг совпадения для результатов поиска."""

from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"[\w\u0400-\u04FF]+", re.UNICODE)


def search_tokens(query: str) -> list[str]:
    """Слова запроса (латиница и кириллица); fallback — вся строка."""
    words = _TOKEN_RE.findall(query.strip())
    if words:
        return words
    stripped = query.strip()
    return [stripped] if stripped else []


def build_search_snippet(
    text: str,
    query: str,
    *,
    context_before: int = 50,
    context_after: int = 50,
) -> str:
    """
    Фрагмент вокруг первого совпадения любого слова из запроса.

    До совпадения — context_before символов, после — context_after (без схлопывания пробелов).
    """
    if not text:
        return ""

    words = search_tokens(query)
    lower_text = text.lower()
    idx = -1
    match_len = 0

    for word in words:
        pos = lower_text.find(word.lower())
        if pos >= 0:
            idx = pos
            match_len = len(word)
            break

    if idx < 0:
        compact = text[: context_before + context_after + 20]
        suffix = "…" if len(text) > len(compact) else ""
        return compact + suffix

    start = max(0, idx - context_before)
    end = min(len(text), idx + match_len + context_after)
    snippet = text[start:end]
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{snippet}{suffix}"
