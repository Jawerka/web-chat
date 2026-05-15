"""
Сервис загрузки и регистрации вложений пользователя.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Attachment
from app.db.repositories import AttachmentRepository, ConversationRepository
from app.integrations.document_extractor import extract_text_from_file, truncate_text
from app.integrations.media_utils import (
    UPLOAD_ROOT,
    asset_media_url,
    is_image_mime,
    safe_filename,
    upload_media_url,
)
from app.services.media_service import MediaService

ALLOWED_MIMES = frozenset({
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
    "text/csv",
})

# Расширения для уточнения MIME, если браузер прислал application/octet-stream
_MIME_BY_EXT: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".csv": "text/csv",
}


class UploadValidationError(Exception):
    """Ошибка валидации загрузки с HTTP-кодом."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class AttachmentService:
    """Регистрация файлов на диске и в SQLite."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = AttachmentRepository(session)
        self._conv_repo = ConversationRepository(session)

    @staticmethod
    def max_bytes() -> int:
        """Максимальный размер одного файла в байтах."""
        return settings.max_upload_mb * 1024 * 1024

    def normalize_mime(self, filename: str, content_type: str | None) -> str:
        """Нормализовать MIME: whitelist + уточнение по расширению."""
        mime = (content_type or "").split(";")[0].strip().lower()
        if mime in ALLOWED_MIMES:
            return mime
        ext = Path(filename).suffix.lower()
        guessed = _MIME_BY_EXT.get(ext)
        if guessed:
            return guessed
        return mime

    def validate_mime(self, mime: str) -> None:
        """Проверить MIME по whitelist."""
        if mime not in ALLOWED_MIMES:
            raise UploadValidationError(
                f"Тип файла не поддерживается: {mime}",
                status_code=415,
            )

    async def register_upload(
        self,
        file: UploadFile,
        *,
        conversation_id: uuid.UUID | None = None,
    ) -> Attachment:
        """
        Сохранить файл на диск и создать запись Attachment.

        Путь: data/uploads/{attachment_id}/{safe_filename}.
        """
        if conversation_id is not None:
            conversation = await self._conv_repo.get_by_id(conversation_id)
            if conversation is None:
                raise UploadValidationError(
                    "Беседа не найдена",
                    status_code=404,
                )

        original_name = file.filename or "file"
        safe_name = safe_filename(original_name)
        if not safe_name:
            raise UploadValidationError(
                "Недопустимое имя файла",
                status_code=400,
            )

        mime = self.normalize_mime(original_name, file.content_type)
        self.validate_mime(mime)

        content = await file.read()
        size = len(content)
        if size > self.max_bytes():
            raise UploadValidationError(
                f"Файл превышает лимит {settings.max_upload_mb} МБ",
                status_code=413,
            )
        if size == 0:
            raise UploadValidationError("Пустой файл", status_code=400)

        attachment_id = uuid.uuid4()
        media_asset_id: uuid.UUID | None = None
        storage_path = ""

        if is_image_mime(mime):
            asset = await MediaService.create_from_bytes_committed(
                content,
                mime,
                conversation_id=conversation_id,
                original_name=original_name,
            )
            media_asset_id = asset.id
        else:
            dest_dir = UPLOAD_ROOT / str(attachment_id)
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_path = dest_dir / safe_name
            dest_path.write_bytes(content)
            storage_path = f"{attachment_id}/{safe_name}"

        return await self._repo.create(
            attachment_id=attachment_id,
            original_name=original_name,
            mime_type=mime,
            size_bytes=size,
            storage_path=storage_path,
            conversation_id=conversation_id,
            media_asset_id=media_asset_id,
        )

    @staticmethod
    def file_path(attachment: Attachment) -> Path:
        """Абсолютный путь к файлу вложения на диске (только документы)."""
        if attachment.media_asset_id is not None or not attachment.storage_path:
            raise ValueError("Вложение хранится в БД, не на диске")
        return UPLOAD_ROOT / attachment.storage_path

    @staticmethod
    def public_url(attachment: Attachment) -> str:
        """URL вложения для чата (относительный путь)."""
        if attachment.media_asset_id is not None:
            return asset_media_url(attachment.media_asset_id)
        filename = Path(attachment.storage_path).name
        return upload_media_url(attachment.id, filename)

    @staticmethod
    def llm_image_url(attachment: Attachment) -> str:
        """Полный URL для LLM vision API."""
        if attachment.media_asset_id is not None:
            return asset_media_url(attachment.media_asset_id, absolute=True)
        filename = Path(attachment.storage_path).name
        return upload_media_url(attachment.id, filename)

    @staticmethod
    def preview_url(attachment: Attachment) -> str | None:
        """URL превью для изображений; для документов — None."""
        if not is_image_mime(attachment.mime_type):
            return None
        return AttachmentService.public_url(attachment)

    async def extract_text(
        self,
        attachment_id: uuid.UUID,
        max_chars: int | None = None,
        *,
        use_cache: bool = True,
    ) -> str:
        """
        Извлечь текст из вложения и сохранить в БД (кэш).

        Args:
            attachment_id: UUID вложения.
            max_chars: Лимит символов в ответе.
            use_cache: Не читать файл повторно, если extracted_text уже есть.

        Returns:
            Текст (возможно обрезанный).

        Raises:
            ValueError: Вложение не найдено или извлечение не удалось.
        """
        limit = max_chars if max_chars is not None else settings.max_extract_chars
        attachment = await self._repo.get_by_id(attachment_id)
        if attachment is None:
            raise ValueError(f"Вложение не найдено: {attachment_id}")

        if use_cache and attachment.extracted_text:
            return truncate_text(attachment.extracted_text, limit)

        path = self.file_path(attachment)
        raw = await asyncio.to_thread(
            extract_text_from_file,
            path,
            attachment.mime_type,
        )
        await self._repo.update_extracted_text(attachment, raw)
        return truncate_text(raw, limit)

    async def prepare_for_llm(
        self,
        attachment_ids: list[uuid.UUID],
    ) -> list[Attachment]:
        """
        Подготовить вложения к отправке в LLM (eager extract для документов).

        Изображения возвращаются без извлечения текста; PDF/DOCX/TXT — с заполненным
        extracted_text в БД.
        """
        prepared: list[Attachment] = []
        for attachment_id in attachment_ids:
            attachment = await self._repo.get_by_id(attachment_id)
            if attachment is None:
                continue
            if is_image_mime(attachment.mime_type):
                prepared.append(attachment)
                continue
            if not attachment.extracted_text:
                try:
                    await self.extract_text(attachment_id, use_cache=False)
                    attachment = await self._repo.get_by_id(attachment_id)
                except ValueError:
                    pass
            if attachment is not None:
                prepared.append(attachment)
        return prepared
