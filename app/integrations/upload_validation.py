"""
Валидация загружаемых файлов: magic bytes, декодирование изображений, сигнатуры документов.
"""

from __future__ import annotations

import io
from typing import NamedTuple

from PIL import Image, UnidentifiedImageError

from app.config import settings


class UploadBytesValidationError(Exception):
    """Содержимое файла не соответствует заявленному типу или лимитам."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class _Sniff(NamedTuple):
    mime: str
    label: str


def sniff_file_kind(data: bytes) -> _Sniff | None:
    """Определить тип по сигнатуре (первые байты)."""
    if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return _Sniff("image/png", "PNG")
    if len(data) >= 2 and data[:2] == b"\xff\xd8":
        return _Sniff("image/jpeg", "JPEG")
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return _Sniff("image/webp", "WebP")
    if len(data) >= 6 and data[:6] in (b"GIF87a", b"GIF89a"):
        return _Sniff("image/gif", "GIF")
    if data.startswith(b"%PDF-"):
        return _Sniff("application/pdf", "PDF")
    if len(data) >= 4 and data[:4] == b"PK\x03\x04":
        return _Sniff(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "ZIP/DOCX",
        )
    return None


def _mime_family(declared: str, sniffed: str) -> bool:
    """Совместимы ли заявленный MIME и сигнатура."""
    if declared == sniffed:
        return True
    if declared.startswith("image/") and sniffed.startswith("image/"):
        return declared == sniffed
    return False


def validate_image_bytes(data: bytes, declared_mime: str) -> None:
    """
    Проверить изображение: сигнатура, декодирование Pillow, лимит пикселей.

    Raises:
        UploadBytesValidationError
    """
    sniff = sniff_file_kind(data)
    if sniff is None or not sniff.mime.startswith("image/"):
        raise UploadBytesValidationError(
            "Файл не распознан как изображение по содержимому",
        )
    if not _mime_family(declared_mime, sniff.mime):
        raise UploadBytesValidationError(
            f"Содержимое ({sniff.label}) не совпадает с типом {declared_mime}",
        )
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            w, h = img.size
    except (UnidentifiedImageError, OSError) as exc:
        raise UploadBytesValidationError(
            f"Не удалось прочитать изображение: {exc}",
        ) from exc
    pixels = w * h
    limit = settings.max_upload_image_pixels
    if pixels > limit:
        raise UploadBytesValidationError(
            f"Изображение {w}×{h} ({pixels} px) превышает лимит {limit} пикселей",
        )


def validate_document_bytes(data: bytes, declared_mime: str) -> None:
    """Проверить сигнатуру PDF/DOCX перед записью на диск."""
    sniff = sniff_file_kind(data)
    if sniff is None:
        if declared_mime in ("text/plain", "text/csv"):
            return
        raise UploadBytesValidationError(
            "Не удалось определить тип файла по содержимому",
        )
    if not _mime_family(declared_mime, sniff.mime):
        raise UploadBytesValidationError(
            f"Содержимое ({sniff.label}) не совпадает с типом {declared_mime}",
        )
