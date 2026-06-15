"""
Сборка сообщений для LLM и пост-обработка ответов ассистента.
"""

from __future__ import annotations

import logging
import re
import uuid
from copy import deepcopy
from typing import Any

from app.db.models import Attachment, Message, MessageRole
from app.diag_logging import log_event
from app.integrations.media_utils import (
    absolute_media_url,
    asset_llm_media_url,
    asset_media_url,
    parse_asset_id_from_url,
    rewrite_image_url_for_llm,
)
from app.public_url import strip_public_base
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.attachment_service import AttachmentService
from app.services.media_service import MediaService
from app.services.prompt_macro_service import expand_macro_text, expand_parts_for_llm

logger = logging.getLogger(__name__)

# Служебные пометки в истории LLM (модель иногда копирует в ответ — убираем при показе/сохранении).
_LLM_IMAGE_CONTEXT_NOTE_RE = re.compile(
    r"\n*\[(?:CTX generated_images:[^\]]*|"
    r"В этом ответе были изображения \(для контекста\):[^\]]*)\]\s*",
    re.IGNORECASE,
)

# Markdown-изображения в тексте ассистента не используем — картинки в content_json + UI.
_MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")

# Legacy generated thumbs и служебные строки SD — не показывать пользователю.
_THUMB_LINE_RE = re.compile(
    r"^\s*Thumbnail:\s*\S+\s*$",
    re.MULTILINE | re.IGNORECASE,
)
_LEGACY_THUMB_URL_RE = re.compile(
    r"https?://\S+/media/generated/thumbs/\S+|/media/generated/thumbs/\S+",
    re.IGNORECASE,
)

# Некоторые модели могут возвращать reasoning в виде тегов `<think>...</think>`.
# Эти теги нельзя оставлять в history для следующего LLM-запроса.
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _init_image_url_for_hint(url: str) -> str | None:
    """Публичный URL asset/generated для подсказки img2img (не /llm)."""
    if not url or not str(url).strip():
        return None
    raw = str(url).strip()
    aid = parse_asset_id_from_url(raw)
    if aid is not None:
        return absolute_media_url(asset_media_url(aid), for_llm=True)
    if raw.startswith("/media/"):
        return absolute_media_url(raw, for_llm=True)
    if raw.startswith("http://") or raw.startswith("https://"):
        path = strip_public_base(raw)
        if path.startswith("/media/"):
            return absolute_media_url(path, for_llm=True)
        return raw
    return raw


def _public_url_from_image_part(part: dict[str, Any]) -> str | None:
    """Публичный URL картинки из part сообщения (не /llm)."""
    raw_asset = part.get("asset_id")
    if raw_asset:
        try:
            return asset_media_url(uuid.UUID(str(raw_asset)), absolute=True, for_llm=True)
        except ValueError:
            pass
    url = (part.get("image_url") or {}).get("url") or ""
    if not url:
        return None
    asset_id = parse_asset_id_from_url(url)
    if asset_id is not None:
        return asset_llm_media_url(asset_id, absolute=True)
    if url.startswith("/media/"):
        return rewrite_image_url_for_llm(url)
    if url.startswith("http://") or url.startswith("https://"):
        return rewrite_image_url_for_llm(url)
    return url


_IMG2IMG_HINT_MARK = "[Для img2img"

# Скрытый префикс из UI img2img (denoising / CFG / число картинок) — не хранить в БД.
_IMG2IMG_GEN_PRESET_PART_RE = re.compile(
    r"^(?:denoising\s+[\d.]+(?:-[\d.]+)?|CFG\s+[\d.]+(?:-[\d.]+)?|"
    r"Сделай\s+\d+\s+изображен\w*)\.?$",
    re.IGNORECASE,
)


def is_img2img_gen_preset_instruction_block(text: str) -> bool:
    """Проверить, что блок — только параметры генерации из панели img2img."""
    normalized = (text or "").strip().rstrip(".")
    if not normalized:
        return False
    parts = [p.strip() for p in normalized.split(";") if p.strip()]
    if not parts:
        return False
    return all(_IMG2IMG_GEN_PRESET_PART_RE.match(p.rstrip(".")) for p in parts)


def strip_img2img_gen_preset_prefix(text: str) -> str:
    """Убрать префикс denoising/CFG/«Сделай N изображений» (для content_text в БД и UI)."""
    raw = text or ""
    trimmed = raw.strip()
    if not trimmed:
        return ""
    sep = trimmed.find("\n\n")
    if sep == -1:
        return "" if is_img2img_gen_preset_instruction_block(trimmed) else raw
    head = trimmed[:sep].strip()
    rest = trimmed[sep + 2 :]
    return rest if is_img2img_gen_preset_instruction_block(head) else raw


def _text_from_parts(parts: list[dict[str, Any]], *, skip_img2img_hints: bool = True) -> str:
    """Собрать текстовое содержимое из parts (без старых подсказок img2img)."""
    chunks: list[str] = []
    for part in parts:
        if part.get("type") != "text":
            continue
        text = str(part.get("text") or "")
        if skip_img2img_hints and text.strip().startswith(_IMG2IMG_HINT_MARK):
            continue
        if text.strip():
            chunks.append(text)
    return "\n\n".join(chunks)


async def filter_available_image_attachments(
    session: AsyncSession,
    attachments: list[Attachment],
) -> list[Attachment]:
    """Оставить только вложения с доступными изображениями (для LLM vision)."""
    media = MediaService(session)
    out: list[Attachment] = []
    dropped = 0
    for att in attachments:
        if not att.mime_type.startswith("image/"):
            out.append(att)
            continue
        url = AttachmentService.llm_image_url(att)
        if await media.is_image_url_available(url):
            out.append(att)
        else:
            dropped += 1
    if dropped:
        log_event(
            logger,
            "llm_vision_filter",
            "dropped unavailable image attachments",
            dropped=dropped,
        )
    return out


async def filter_unreachable_image_parts(
    session: AsyncSession,
    parts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Убрать image_url parts, указывающие на отсутствующие файлы/ассеты."""
    media = MediaService(session)
    out: list[dict[str, Any]] = []
    dropped = 0
    for part in parts:
        if part.get("type") != "image_url":
            out.append(part)
            continue
        raw_asset = part.get("asset_id")
        if raw_asset:
            try:
                aid = uuid.UUID(str(raw_asset))
            except ValueError:
                dropped += 1
                continue
            if await media.asset_exists(aid):
                out.append(part)
            else:
                dropped += 1
            continue
        url = (part.get("image_url") or {}).get("url") or ""
        if await media.is_image_url_available(url):
            out.append(part)
        else:
            dropped += 1
    if dropped:
        log_event(
            logger,
            "llm_vision_filter",
            "dropped unreachable image_url parts",
            dropped=dropped,
        )
    return out


async def sanitize_llm_messages_for_vision(
    session: AsyncSession,
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Убрать недоступные image_url из multimodal content перед запросом к LLM."""
    sanitized: list[dict[str, Any]] = []
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            sanitized.append(msg)
            continue
        filtered = await filter_unreachable_image_parts(session, content)
        sanitized.append({**msg, "content": filtered})
    return sanitized


def refresh_user_parts_for_regenerate(
    parts: list[dict[str, Any]],
    attachments: list[Attachment],
    *,
    fallback_text: str = "",
) -> list[dict[str, Any]]:
    """
    Пересобрать multimodal parts перед перегенерацией.

    Вложения — источник истины для image_url; устаревшие подсказки img2img убираются.
    """
    text = (fallback_text or "").strip() or _text_from_parts(parts)
    if attachments:
        rebuilt = build_user_content(text, attachments)
        image_n = sum(1 for p in rebuilt if p.get("type") == "image_url")
        log_event(
            logger,
            "regenerate_parts",
            "rebuilt user parts from attachments",
            attachment_count=len(attachments),
            image_parts=image_n,
        )
        return rebuilt
    kept: list[dict[str, Any]] = []
    for part in parts:
        if part.get("type") == "text":
            t = str(part.get("text") or "")
            if t.strip().startswith(_IMG2IMG_HINT_MARK):
                continue
        kept.append(deepcopy(part))
    if text and not any(p.get("type") == "text" for p in kept):
        kept.insert(0, {"type": "text", "text": text})
    return kept or [{"type": "text", "text": text or ""}]


def collect_img2img_init_lines(
    attachments: list[Attachment],
    parts: list[dict[str, Any]] | None = None,
) -> list[str]:
    """
    Строки attachment_id / init_image_url для подсказки LLM и логов.

    Источники: строки Attachment в БД и image_url parts (если message_id не привязан).
    """
    lines: list[str] = []
    seen_urls: set[str] = set()

    for att in attachments:
        if not att.mime_type.startswith("image/"):
            continue
        url = _init_image_url_for_hint(AttachmentService.public_url(att))
        if not url:
            continue
        seen_urls.add(url)
        lines.append(f"attachment_id={att.id}")
        lines.append(f"init_image_url={url}")

    for part in parts or []:
        if part.get("type") != "image_url":
            continue
        raw = (part.get("image_url") or {}).get("url") or ""
        if part.get("asset_id"):
            try:
                raw = asset_media_url(uuid.UUID(str(part["asset_id"])))
            except ValueError:
                pass
        url = _init_image_url_for_hint(raw)
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        lines.append(f"init_image_url={url}")

    return lines


def build_img2img_init_hint_text(
    attachments: list[Attachment],
    parts: list[dict[str, Any]] | None = None,
) -> str:
    """Явные attachment_id и URL для img2img — модель часто не связывает vision с tool args."""
    lines = collect_img2img_init_lines(attachments, parts)
    if not lines:
        return ""
    return (
        "[Для img2img используйте эти параметры исходника (скопируйте в вызов инструмента):]\n"
        + "\n".join(lines)
    )


def append_img2img_init_hints(
    parts: list[dict[str, Any]],
    attachments: list[Attachment],
    *,
    image_parts: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Добавить текстовую подсказку с ID/URL вложений-картинок."""
    hint = build_img2img_init_hint_text(attachments, image_parts if image_parts is not None else parts)
    if not hint:
        return parts
    out = list(parts)
    out.append({"type": "text", "text": hint})
    return out


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
            parts.append(
                {
                    "type": "text",
                    "text": f"[Изображение: {att.original_name}]",
                }
            )
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


def strip_llm_image_context_note(text: str) -> str:
    """Убрать служебную пометку о сгенерированных картинках (эхо из контекста LLM)."""
    if not text:
        return text
    result = _LLM_IMAGE_CONTEXT_NOTE_RE.sub("", text)
    result = re.sub(r"[ \t]+\n", "\n", result)
    return re.sub(r"\n{3,}", "\n\n", result).strip()


def _image_context_tokens(urls: list[str]) -> list[str]:
    """Короткие идентификаторы картинок для служебной пометки в истории LLM."""
    tokens: list[str] = []
    seen: set[str] = set()
    for url in urls:
        aid = parse_asset_id_from_url(url)
        if aid is not None:
            token = str(aid)
        else:
            m = re.search(r"/media/generated/([^/\s?#]+)", url)
            token = f"disk:{m.group(1)}" if m else ""
        if token and token not in seen:
            seen.add(token)
            tokens.append(token)
    return tokens[:8]


def _image_urls_from_content_json(content_json: dict[str, Any]) -> list[str]:
    """URL картинок из content_json assistant/user (images + image_asset_ids)."""
    urls: list[str] = []
    seen: set[str] = set()
    for raw in content_json.get("images") or []:
        u = str(raw).strip()
        if not u:
            continue
        aid = parse_asset_id_from_url(u)
        if aid is not None:
            u = asset_media_url(aid)
        if u not in seen:
            seen.add(u)
            urls.append(u)
    for raw in content_json.get("image_asset_ids") or []:
        try:
            u = asset_media_url(uuid.UUID(str(raw)))
        except ValueError:
            continue
        if u not in seen:
            seen.add(u)
            urls.append(u)
    return urls


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
        cj = message.content_json if isinstance(message.content_json, dict) else {}
        image_urls = _image_urls_from_content_json(cj) if cj else []
        # Для следующего запроса LLM не передаём `<think>...</think>`.
        text = strip_think_tags(message.content_text or "")
        tool_calls = cj.get("tool_calls") if cj else None

        entry: dict[str, Any] = {"role": "assistant", "content": text or None}
        if tool_calls:
            entry["tool_calls"] = tool_calls
            if image_urls:
                tokens = _image_context_tokens(image_urls)
                if tokens:
                    suffix = (
                        f"\n\n[CTX generated_images: {', '.join(tokens)} | "
                        "служебная пометка для контекста, не цитируй пользователю]"
                    )
                    entry["content"] = (text + suffix).strip() if text else suffix.strip()
        elif image_urls:
            parts: list[dict[str, Any]] = []
            if text:
                parts.append({"type": "text", "text": text})
            else:
                parts.append(
                    {
                        "type": "text",
                        "text": "[Изображения, сгенерированные ассистентом в этом сообщении]",
                    },
                )
            for url in image_urls:
                parts.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": rewrite_image_url_for_llm(url)},
                    },
                )
            entry["content"] = parts
        return entry

    return {"role": message.role.value, "content": message.content_text or ""}


def history_to_llm_messages(
    messages: list[Message],
    *,
    alias_to_body: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Список Message → история для LLM."""
    return [message_to_llm_dict(m, alias_to_body=alias_to_body) for m in messages]


def canonical_stored_image_urls(
    urls: list[str] | None,
    asset_ids: list[str] | None,
) -> list[str]:
    """
    URL для content_json.images: только /media/asset/{uuid}, если есть asset_ids.

    Legacy /media/generated/… в urls игнорируются — файлы после ingest на диске не живут.
    """
    ids = asset_ids or []
    if ids:
        out: list[str] = []
        seen: set[str] = set()
        for raw in ids:
            try:
                u = asset_media_url(uuid.UUID(str(raw)))
            except ValueError:
                continue
            if u not in seen:
                seen.add(u)
                out.append(u)
        return out
    out: list[str] = []
    seen: set[str] = set()
    for raw in urls or []:
        u = str(raw).strip()
        if not u or u in seen:
            continue
        aid = parse_asset_id_from_url(u)
        if aid is not None:
            u = asset_media_url(aid)
        elif "/media/generated/" in u:
            continue
        seen.add(u)
        out.append(u)
    return out


def rewrite_media_urls_in_text(text: str, url_map: dict[str, str]) -> str:
    """Заменить устаревшие URL картинок в тексте (markdown и plain)."""
    if not text or not url_map:
        return text
    result = text
    for old, new in sorted(url_map.items(), key=lambda x: len(x[0]), reverse=True):
        result = result.replace(old, new)
    return result


def strip_legacy_thumb_urls_from_text(text: str) -> str:
    """Убрать устаревшие /media/generated/thumbs/… из prose ассистента."""
    if not text:
        return text
    result = _THUMB_LINE_RE.sub("", text)
    result = _LEGACY_THUMB_URL_RE.sub("", result)
    result = re.sub(r"[ \t]+\n", "\n", result)
    return re.sub(r"\n{3,}", "\n\n", result).strip()


def strip_markdown_images(text: str) -> str:
    """Убрать ![alt](url) из текста (картинки показывает UI по content_json.images)."""
    if not text:
        return text
    result = _MARKDOWN_IMAGE_RE.sub("", text)
    result = re.sub(r"[ \t]+\n", "\n", result)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def strip_think_tags(text: str) -> str:
    """Убрать `<think>...</think>` и одиночные `<think>`/`</think>`."""
    if not text:
        return text
    result = _THINK_BLOCK_RE.sub("", text)
    # На случай “некорректно сформированных” тегов.
    result = re.sub(r"</?think>", "", result, flags=re.IGNORECASE)
    result = re.sub(r"[ \t]+\n", "\n", result)
    return re.sub(r"\n{3,}", "\n\n", result).strip()


def finalize_assistant_text(
    completion_content: str | None,
    *,
    media_url_rewrites: dict[str, str] | None = None,
) -> str:
    """Текст ответа ассистента без markdown-картинок и с актуальными URL в prose."""
    body = completion_content or ""
    if media_url_rewrites:
        body = rewrite_media_urls_in_text(body, media_url_rewrites)
    body = strip_legacy_thumb_urls_from_text(body)
    body = strip_markdown_images(body)
    body = strip_llm_image_context_note(body)
    return strip_think_tags(body)
