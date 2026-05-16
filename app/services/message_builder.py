"""
Сборка сообщений для LLM и пост-обработка ответов ассистента.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from app.db.models import Attachment, Message, MessageRole
from app.integrations.media_utils import rewrite_image_url_for_llm
from app.services.attachment_service import AttachmentService
from app.services.prompt_macro_service import expand_macro_text, expand_parts_for_llm

# Markdown-изображения в тексте ассистента не используем — картинки в content_json + UI.
_MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")


def build_user_content(
    text: str,
    attachments: list[Attachment],
) -> list[dict[str, Any]]:
    """
    Собрать multimodal content для сообщения пользователя.

    Изображения — image_url; документы — текстовые блоки с extracted_text.
    """
    parts: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for att in attachments:
        if att.mime_type.startswith("image/"):
            part: dict[str, Any] = {
                "type": "image_url",
                "image_url": {"url": AttachmentService.public_url(att)},
            }
            if att.media_asset_id is not None:
                part["asset_id"] = str(att.media_asset_id)
            parts.append(part)
        elif att.extracted_text:
            parts.append(
                {
                    "type": "text",
                    "text": f"[Документ: {att.original_name}]\n{att.extracted_text}",
                }
            )
    return parts


def message_to_llm_dict(
    message: Message,
    *,
    alias_to_body: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Преобразовать сохранённое сообщение в формат OpenAI API."""
    macros = alias_to_body or {}
    if message.role == MessageRole.USER:
        if message.content_json and "parts" in message.content_json:
            parts = deepcopy(message.content_json["parts"])
            if macros:
                parts = expand_parts_for_llm(parts, macros)
            for part in parts:
                if part.get("type") == "image_url" and part.get("image_url", {}).get("url"):
                    part["image_url"]["url"] = rewrite_image_url_for_llm(
                        part["image_url"]["url"],
                    )
            return {"role": "user", "content": parts}
        text = message.content_text or ""
        if macros:
            text = expand_macro_text(text, macros)
        return {"role": "user", "content": text}

    if message.role == MessageRole.ASSISTANT:
        entry: dict[str, Any] = {
            "role": "assistant",
            "content": message.content_text,
        }
        if message.content_json and message.content_json.get("tool_calls"):
            entry["tool_calls"] = message.content_json["tool_calls"]
        return entry

    return {"role": message.role.value, "content": message.content_text or ""}


def history_to_llm_messages(
    messages: list[Message],
    *,
    alias_to_body: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Список Message → история для LLM."""
    return [message_to_llm_dict(m, alias_to_body=alias_to_body) for m in messages]


def rewrite_media_urls_in_text(text: str, url_map: dict[str, str]) -> str:
    """Заменить устаревшие URL картинок в тексте (markdown и plain)."""
    if not text or not url_map:
        return text
    result = text
    for old, new in sorted(url_map.items(), key=lambda x: len(x[0]), reverse=True):
        result = result.replace(old, new)
    return result


def strip_markdown_images(text: str) -> str:
    """Убрать ![alt](url) из текста (картинки показывает UI по content_json.images)."""
    if not text:
        return text
    result = _MARKDOWN_IMAGE_RE.sub("", text)
    result = re.sub(r"[ \t]+\n", "\n", result)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def finalize_assistant_text(
    completion_content: str | None,
    *,
    media_url_rewrites: dict[str, str] | None = None,
) -> str:
    """Текст ответа ассистента без markdown-картинок и с актуальными URL в prose."""
    body = completion_content or ""
    if media_url_rewrites:
        body = rewrite_media_urls_in_text(body, media_url_rewrites)
    return strip_markdown_images(body)
