"""
Утилиты для безопасной работы с файлами: uploads и generated (SD).

Портировано из image-gen: safe_filename, save_image_from_base64, make_thumbnail.
"""

from __future__ import annotations

import base64
import logging
import uuid
from pathlib import Path

from PIL import Image

from app.config import settings

logger = logging.getLogger(__name__)

UPLOAD_ROOT = Path("data/uploads")
GENERATED_ROOT = Path("data/generated")
GENERATED_THUMB_ROOT = GENERATED_ROOT / "thumbs"


def ensure_media_directories() -> None:
    """Создать каталоги uploads и generated при старте."""
    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    GENERATED_ROOT.mkdir(parents=True, exist_ok=True)
    GENERATED_THUMB_ROOT.mkdir(parents=True, exist_ok=True)


def safe_filename(filename: str) -> str:
    """
    Санитизация имени файла от path traversal и опасных символов.

    Args:
        filename: Исходное имя файла.

    Returns:
        Безопасное имя или пустая строка, если имя недопустимо.
    """
    safe = Path(filename).name
    if not safe or all(c == "." for c in safe):
        return ""

    allowed = set(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-."
    )
    result = "".join(c for c in safe if c in allowed)
    return result if result else ""


def generate_filename(prefix: str = "sd", extension: str = "png") -> str:
    """Уникальное имя файла: {prefix}_{uuid}.{extension}."""
    return f"{prefix}_{uuid.uuid4().hex}.{extension}"


def save_image(data: bytes, filename: str | None = None) -> str:
    """Сохранить бинарные данные изображения в data/generated/."""
    name = filename or generate_filename()
    path = GENERATED_ROOT / name
    path.write_bytes(data)
    logger.info("Сохранено изображение: %s (%d байт)", name, len(data))
    return name


def save_image_from_base64(b64_data: str, filename: str | None = None) -> str:
    """Декодировать base64 (или data URL) и сохранить PNG в generated/."""
    if "," in b64_data:
        _, b64 = b64_data.split(",", 1)
    else:
        b64 = b64_data
    return save_image(base64.b64decode(b64), filename)


def make_thumbnail(
    filename: str,
    max_size: tuple[int, int] = (512, 512),
    quality: int = 85,
) -> str | None:
    """
    Создать JPEG-миниатюру в data/generated/thumbs/.

    Returns:
        Имя файла миниатюры или None при ошибке.
    """
    src = GENERATED_ROOT / filename
    if not src.exists():
        logger.error("Миниатюра: исходник не найден %s", src)
        return None

    thumb_name = Path(filename).stem + ".jpg"
    dst = GENERATED_THUMB_ROOT / thumb_name

    try:
        with Image.open(src) as img:
            img.thumbnail(max_size)
            if img.mode in ("RGBA", "P", "LA"):
                img = img.convert("RGB")
            img.save(dst, "JPEG", quality=quality)
        logger.info("Создана миниатюра: %s", thumb_name)
        return thumb_name
    except OSError as exc:
        logger.error("Ошибка миниатюры для %s: %s", filename, exc)
        return None


def resolve_generated_file(filename: str, *, thumbs: bool = False) -> Path:
    """
    Безопасно разрешить путь к файлу в generated/ или thumbs/.

    Raises:
        ValueError: Недопустимое имя или выход за каталог.
        FileNotFoundError: Файл не найден.
    """
    safe = safe_filename(filename)
    if not safe:
        raise ValueError("Недопустимое имя файла")

    base = (GENERATED_THUMB_ROOT if thumbs else GENERATED_ROOT).resolve()
    path = (base / safe).resolve()

    if not path.is_relative_to(base):
        raise ValueError("Путь выходит за пределы каталога")

    if not path.is_file():
        raise FileNotFoundError(safe)

    return path


def absolute_media_url(url: str) -> str:
    """Относительный /media/… → полный URL с PUBLIC_BASE_URL."""
    if url.startswith("/media/"):
        return f"{settings.public_base_url.rstrip('/')}{url}"
    return url


def asset_media_url(asset_id: uuid.UUID, *, absolute: bool = False) -> str:
    """
    URL изображения из БД.

    По умолчанию относительный (/media/asset/…) — стабилен при перезагрузке UI.
    absolute=True — полный URL для LLM vision (PUBLIC_BASE_URL).
    """
    path = f"/media/asset/{asset_id}"
    if absolute:
        return f"{settings.public_base_url.rstrip('/')}{path}"
    return path


def generated_media_url(filename: str) -> str:
    """Публичный URL сгенерированного изображения (legacy, до ingest в БД)."""
    safe = safe_filename(filename)
    base = settings.public_base_url.rstrip("/")
    return f"{base}/media/generated/{safe}"


def generated_thumb_url(thumb_filename: str) -> str:
    """Публичный URL миниатюры."""
    safe = safe_filename(thumb_filename)
    base = settings.public_base_url.rstrip("/")
    return f"{base}/media/generated/thumbs/{safe}"


def attachment_dir(attachment_id: uuid.UUID) -> Path:
    """Каталог хранения для одного вложения."""
    return UPLOAD_ROOT / str(attachment_id)


def resolve_upload_file(
    attachment_id: uuid.UUID,
    filename: str,
) -> Path:
    """
    Разрешить путь к файлу вложения с проверкой безопасности.

    Raises:
        ValueError: Недопустимое имя или выход за пределы каталога.
        FileNotFoundError: Файл не существует.
    """
    safe = safe_filename(filename)
    if not safe:
        raise ValueError("Недопустимое имя файла")

    base = attachment_dir(attachment_id).resolve()
    path = (base / safe).resolve()

    if not path.is_relative_to(base):
        raise ValueError("Путь выходит за пределы каталога вложения")

    if not path.is_file():
        raise FileNotFoundError(safe)

    return path


def upload_media_url(attachment_id: uuid.UUID, filename: str) -> str:
    """Публичный URL файла для браузера и LLM vision."""
    safe = safe_filename(filename)
    base = settings.public_base_url.rstrip("/")
    return f"{base}/media/uploads/{attachment_id}/{safe}"


def is_image_mime(mime_type: str) -> bool:
    """Проверить, что MIME относится к изображению."""
    return mime_type.startswith("image/")
