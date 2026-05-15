"""
Сборка сообщений для LLM и пост-обработка ответов ассистента.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.db.models import Attachment, Message, MessageRole
from app.integrations.media_utils import absolute_media_url
from app.services.attachment_service import AttachmentService


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
            parts.append({
                "type": "text",
                "text": f"[Документ: {att.original_name}]\n{att.extracted_text}",
            })
    return parts


def message_to_llm_dict(message: Message) -> dict[str, Any]:
    """Преобразовать сохранённое сообщение в формат OpenAI API."""
    if message.role == MessageRole.USER:
        if message.content_json and "parts" in message.content_json:
            parts = deepcopy(message.content_json["parts"])
            for part in parts:
                if part.get("type") == "image_url" and part.get("image_url", {}).get("url"):
                    part["image_url"]["url"] = absolute_media_url(part["image_url"]["url"])
            return {"role": "user", "content": parts}
        return {"role": "user", "content": message.content_text or ""}

    if message.role == MessageRole.ASSISTANT:
        entry: dict[str, Any] = {
            "role": "assistant",
            "content": message.content_text,
        }
        if message.content_json and message.content_json.get("tool_calls"):
            entry["tool_calls"] = message.content_json["tool_calls"]
        return entry

    return {"role": message.role.value, "content": message.content_text or ""}


def history_to_llm_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """Список Message → история для LLM."""
    return [message_to_llm_dict(m) for m in messages]


def rewrite_media_urls_in_text(text: str, url_map: dict[str, str]) -> str:
    """Заменить устаревшие URL картинок в тексте (markdown и plain)."""
    if not text or not url_map:
        return text
    result = text
    for old, new in sorted(url_map.items(), key=lambda x: len(x[0]), reverse=True):
        result = result.replace(old, new)
    return result


def append_images_markdown(text: str, urls: list[str]) -> str:
    """Добавить markdown-изображения в конец ответа, если их ещё нет."""
    result = text
    for url in urls:
        if url not in result:
            result += f"\n\n![Сгенерированное изображение]({url})"
    return result
