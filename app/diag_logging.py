"""
Структурированные диагностические записи для журнала UI и файла.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

_ASSET_ID_RE = re.compile(
    r"/media/asset/([0-9a-fA-F-]{36})",
    re.IGNORECASE,
)


def redact_url(url: str) -> str:
    """URL для лога: хост + путь без query, asset id сокращён."""
    if not url or not isinstance(url, str):
        return ""
    u = url.strip()
    if u.startswith("data:"):
        return f"data:[{len(u)} chars]"
    try:
        p = urlparse(u)
        path = p.path or u
        path = _ASSET_ID_RE.sub("/media/asset/…", path)
        if p.netloc:
            return f"{p.scheme or 'http'}://{p.netloc}{path}"
        return path
    except Exception:
        return u[:120]


def summarize_llm_messages(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Сводка payload для LLM без полного текста."""
    roles: dict[str, int] = {}
    text_parts = 0
    image_parts = 0
    image_urls: list[str] = []
    tool_msgs = 0

    for msg in messages:
        role = str(msg.get("role") or "?")
        roles[role] = roles.get(role, 0) + 1
        content = msg.get("content")
        if role == "tool":
            tool_msgs += 1
        if isinstance(content, str):
            text_parts += 1
            continue
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            ptype = part.get("type")
            if ptype == "text":
                text_parts += 1
            elif ptype == "image_url":
                image_parts += 1
                raw = (part.get("image_url") or {}).get("url") or ""
                if raw:
                    image_urls.append(redact_url(str(raw)))
            elif part.get("asset_id"):
                image_parts += 1
                image_urls.append(f"asset:{str(part['asset_id'])[:8]}…")

    return {
        "message_count": len(messages),
        "roles": roles,
        "text_parts": text_parts,
        "image_parts": image_parts,
        "image_urls": image_urls[:12],
        "tool_messages": tool_msgs,
    }


def log_event(
    logger: logging.Logger,
    event: str,
    message: str,
    *,
    level: int = logging.INFO,
    **fields: Any,
) -> None:
    """Запись с полем event и произвольными полями (JsonLogFormatter / UI)."""
    extra: dict[str, Any] = {"event": event}
    for key, val in fields.items():
        if val is not None and val != "":
            extra[key] = val
    logger.log(level, message, extra=extra)
