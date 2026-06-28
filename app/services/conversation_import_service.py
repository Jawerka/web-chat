"""
Импорт изображения + текста в новую беседу (черновик composer, без отправки агенту).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import AttachmentOut, ConversationFromImageOut
from app.config import settings
from app.constants import DEFAULT_CONVERSATION_TITLE
from app.db.models import Preset
from app.db.repositories import ConversationRepository, PresetRepository
from app.integrations.media_utils import (
    is_image_mime,
    parse_asset_id_from_url,
    resolve_generated_file,
    safe_generated_filename,
    sniff_image_mime,
)
from app.integrations.upload_validation import UploadBytesValidationError, validate_image_bytes
from app.services.attachment_service import AttachmentService, UploadValidationError
from app.services.gallery_owner import assert_gallery_media_access
from app.services.media_service import MediaService
from app.services.request_user import RequestUser, owner_user_id_for_request
from app.services.user_quotas import ensure_can_create_conversation, ensure_can_upload

_GENERATED_URL_RE = re.compile(
    r"/media/generated/(?:thumbs/)?([^\s\)?#]+\.(?:png|jpg|jpeg|webp|gif))",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ImageImportSource:
    """Ровно один источник изображения для импорта."""

    asset_id: uuid.UUID | None = None
    disk_filename: str | None = None
    url: str | None = None
    upload_file: UploadFile | None = None


class ConversationImportError(Exception):
    """Ошибка импорта с HTTP-кодом."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def image_source_from_json(image: dict | None) -> ImageImportSource:
    """Разобрать JSON-поле image (ровно одно поле)."""
    if not image or not isinstance(image, dict):
        raise ConversationImportError("Не указан источник изображения (image)")

    asset_id = image.get("asset_id")
    disk_filename = image.get("disk_filename")
    url = image.get("url")

    present = [
        name
        for name, value in (
            ("asset_id", asset_id),
            ("disk_filename", disk_filename),
            ("url", url),
        )
        if value is not None and str(value).strip()
    ]
    if len(present) != 1:
        raise ConversationImportError(
            "Укажите ровно одно поле в image: asset_id, disk_filename или url",
        )

    if asset_id is not None:
        try:
            parsed = uuid.UUID(str(asset_id))
        except ValueError as exc:
            raise ConversationImportError("Некорректный asset_id") from exc
        return ImageImportSource(asset_id=parsed)

    if disk_filename is not None:
        name = str(disk_filename).strip()
        if not name:
            raise ConversationImportError("Пустой disk_filename")
        return ImageImportSource(disk_filename=name)

    return ImageImportSource(url=str(url).strip())


def image_source_from_multipart(
    *,
    upload_file: UploadFile | None,
) -> ImageImportSource:
    """Источник из multipart (обязателен файл image)."""
    if upload_file is None:
        raise ConversationImportError("Не передан файл image")
    return ImageImportSource(upload_file=upload_file)


async def _resolve_preset(
    session: AsyncSession,
    *,
    preset_slug: str | None,
) -> Preset:
    preset_repo = PresetRepository(session)
    if preset_slug and preset_slug.strip():
        preset = await preset_repo.get_by_slug(preset_slug.strip())
        if preset is None:
            raise ConversationImportError("Пресет не найден", status_code=404)
        return preset
    preset = await preset_repo.get_default()
    if preset is None:
        raise ConversationImportError(
            "Не настроен пресет по умолчанию",
            status_code=500,
        )
    return preset


def _parse_url_source(url: str) -> ImageImportSource:
    """Разобрать url на asset_id или disk_filename."""
    stripped = url.strip()
    asset_id = parse_asset_id_from_url(stripped)
    if asset_id is not None:
        return ImageImportSource(asset_id=asset_id)

    match = _GENERATED_URL_RE.search(stripped)
    if match:
        filename = safe_generated_filename(match.group(1)) or match.group(1)
        return ImageImportSource(disk_filename=filename)

    return ImageImportSource(url=stripped)


async def _attachment_from_source(
    session: AsyncSession,
    *,
    source: ImageImportSource,
    conversation_id: uuid.UUID,
    user: RequestUser | None,
) -> AttachmentOut:
    attachment_service = AttachmentService(session)
    media_service = MediaService(session)

    if source.asset_id is not None:
        asset = await media_service.get_asset(source.asset_id)
        if asset is None:
            raise ConversationImportError("MediaAsset не найден", status_code=404)
        try:
            await assert_gallery_media_access(session, asset, user)
        except PermissionError as exc:
            raise ConversationImportError("Нет доступа к изображению", status_code=403) from exc
        try:
            attachment = await attachment_service.create_from_media_asset(
                asset=asset,
                conversation_id=conversation_id,
            )
        except UploadValidationError as exc:
            raise ConversationImportError(exc.message, status_code=exc.status_code) from exc
        return _attachment_out(attachment_service, attachment)

    if source.disk_filename is not None:
        await ensure_can_upload(session, user, new_files=1)
        safe = safe_generated_filename(source.disk_filename) or source.disk_filename
        try:
            path = resolve_generated_file(safe, thumbs=False)
        except (FileNotFoundError, ValueError) as exc:
            raise ConversationImportError("Файл генерации не найден", status_code=404) from exc
        data = path.read_bytes()
        mime = sniff_image_mime(data) or "image/png"
        if not is_image_mime(mime):
            raise ConversationImportError("Только изображения", status_code=415)
        try:
            validate_image_bytes(data, mime)
        except UploadBytesValidationError as exc:
            raise ConversationImportError(exc.message, status_code=415) from exc
        return await _attachment_from_bytes(
            attachment_service,
            data=data,
            mime=mime,
            original_name=safe,
            conversation_id=conversation_id,
        )

    if source.upload_file is not None:
        await ensure_can_upload(session, user, new_files=1)
        raw = await source.upload_file.read()
        name = source.upload_file.filename or "image.png"
        mime = attachment_service.normalize_mime(
            name,
            source.upload_file.content_type,
        )
        try:
            attachment = await attachment_service.register_image_bytes(
                raw,
                mime=mime,
                original_name=name,
                conversation_id=conversation_id,
            )
        except UploadValidationError as exc:
            raise ConversationImportError(exc.message, status_code=exc.status_code) from exc
        return _attachment_out(attachment_service, attachment)

    if source.url is not None:
        await ensure_can_upload(session, user, new_files=1)
        resolved = _parse_url_source(source.url)
        if resolved.asset_id is not None or resolved.disk_filename is not None:
            return await _attachment_from_source(
                session,
                source=resolved,
                conversation_id=conversation_id,
                user=user,
            )
        try:
            fetch_url = source.url
            if source.url.startswith("/media/"):
                fetch_url = f"http://127.0.0.1:{settings.web_port}{source.url}"
            data, mime = await media_service._load_image_bytes(fetch_url, source.url)
        except Exception as exc:
            raise ConversationImportError(
                f"Не удалось загрузить изображение: {exc}",
                status_code=400,
            ) from exc
        if not is_image_mime(mime):
            raise ConversationImportError("Только изображения", status_code=415)
        name = source.url.rsplit("/", 1)[-1] or "image.png"
        return await _attachment_from_bytes(
            attachment_service,
            data=data,
            mime=mime,
            original_name=name,
            conversation_id=conversation_id,
        )

    raise ConversationImportError("Не указан источник изображения")


async def _attachment_from_bytes(
    attachment_service: AttachmentService,
    *,
    data: bytes,
    mime: str,
    original_name: str,
    conversation_id: uuid.UUID,
) -> AttachmentOut:
    try:
        attachment = await attachment_service.register_image_bytes(
            data,
            mime=mime,
            original_name=original_name,
            conversation_id=conversation_id,
        )
    except UploadValidationError as exc:
        raise ConversationImportError(exc.message, status_code=exc.status_code) from exc
    return _attachment_out(attachment_service, attachment)


def _attachment_out(service: AttachmentService, attachment) -> AttachmentOut:
    return AttachmentOut(
        id=attachment.id,
        original_name=attachment.original_name,
        mime_type=attachment.mime_type,
        size_bytes=attachment.size_bytes,
        preview_url=service.preview_url(attachment),
    )


async def create_conversation_from_image(
    session: AsyncSession,
    *,
    source: ImageImportSource,
    text: str | None = None,
    title: str | None = None,
    preset_slug: str | None = "img2img",
    user: RequestUser | None = None,
) -> ConversationFromImageOut:
    """
    Создать беседу, прикрепить изображение, вернуть данные для composer (без user message).
    """
    preset = await _resolve_preset(session, preset_slug=preset_slug)
    conv_title = title.strip() if title and title.strip() else DEFAULT_CONVERSATION_TITLE
    await ensure_can_create_conversation(session, user)

    conv_repo = ConversationRepository(session)
    composer_text = (text or "").strip()
    conversation = await conv_repo.create(
        title=conv_title,
        preset_id=preset.id,
        owner_user_id=owner_user_id_for_request(user),
        composer_draft_text=composer_text or None,
    )

    attachment_out = await _attachment_from_source(
        session,
        source=source,
        conversation_id=conversation.id,
        user=user,
    )

    composer_text = (text or "").strip()
    chat_url = f"/?conv={conversation.id}"

    await session.commit()

    return ConversationFromImageOut(
        conversation_id=conversation.id,
        title=conversation.title,
        preset_id=conversation.preset_id,
        attachments=[attachment_out],
        composer_text=composer_text,
        chat_url=chat_url,
    )
