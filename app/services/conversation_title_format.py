"""
Форматирование заголовков бесед для отображения и нормализации.
"""

from __future__ import annotations

import re

# Длинные префиксы — раньше в списке (сначала самые специфичные).
_GENERIC_TITLE_PREFIXES: tuple[str, ...] = (
    "генерация изображений",
    "генерация изображения",
    "создание изображений",
    "создание изображения",
    "генерация картинок",
    "генерация картинки",
    "создание картинки",
    "создание картинок",
    "генерация",
    "создание",
)

_PREFIX_SEP_RE = re.compile(r"^[\s:—–\-…\.]+")


def strip_generic_conversation_title_prefix(title: str) -> str:
    """
    Убрать шаблонное начало («Генерация изображений …»), оставить суть темы.

    Если после обрезки остаётся слишком мало текста — вернуть исходное название.
    """
    text = (title or "").strip()
    if not text:
        return ""

    lower = text.casefold()
    for prefix in _GENERIC_TITLE_PREFIXES:
        if not lower.startswith(prefix):
            continue
        rest = _PREFIX_SEP_RE.sub("", text[len(prefix) :]).strip()
        if len(rest) >= 2 and rest not in ("...", "…"):
            return rest
        # Совпал шаблонный префикс, но темы нет — не укорачиваем до «изображений» и т.п.
        return text
    return text


def format_conversation_title_for_display(title: str) -> str:
    """Короткое имя для списка бесед (без общих префиксов генерации)."""
    stripped = strip_generic_conversation_title_prefix(title)
    return stripped or (title or "").strip()
