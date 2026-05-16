"""
Утилиты для безопасной работы с файлами: uploads и generated (SD).

Портировано из image-gen: safe_filename, save_image_from_base64, make_thumbnail.
"""

from __future__ import annotations

import base64
import io
import logging
import re
import uuid
from pathlib import Path

from PIL import Image

from app.config import settings

logger = logging.getLogger(__name__)

_ASSET_URL_RE = re.compile(
    r"/media/asset/([0-9a-fA-F-]{36})(?:/(?:thumb|llm))?",
    re.IGNORECASE,
)
_UPLOAD_URL_RE = re.compile(
    r"/media/uploads/([0-9a-fA-F-]{36})/([^/\s\)?#]+)",
    re.IGNORECASE,
)

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

    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-.")
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


def asset_llm_media_url(asset_id: uuid.UUID, *, absolute: bool = False) -> str:
    """URL сжатой копии для vision API (GET /media/asset/{id}/llm)."""
    path = f"/media/asset/{asset_id}/llm"
    if absolute:
        return f"{settings.public_base_url.rstrip('/')}{path}"
    return path


def parse_upload_from_url(url: str) -> tuple[uuid.UUID, str] | None:
    """Извлечь attachment_id и имя файла из URL вложения."""
    m = _UPLOAD_URL_RE.search(url)
    if not m:
        return None
    try:
        return uuid.UUID(m.group(1)), m.group(2)
    except ValueError:
        return None


def parse_asset_id_from_url(url: str) -> uuid.UUID | None:
    """Извлечь UUID media asset из URL, если есть."""
    m = _ASSET_URL_RE.search(url)
    if not m:
        return None
    try:
        return uuid.UUID(m.group(1))
    except ValueError:
        return None


def rewrite_image_url_for_llm(url: str) -> str:
    """
    Абсолютный URL для LLM; asset-изображения — вариант /llm (JPEG ≤ llm_vision_max_bytes).

    llama-server принимает только http(s)://, file:// или data:image/…;base64,… —
    относительный /media/… даёт «Invalid url value».
    """
    if not url:
        return url
    asset_id = parse_asset_id_from_url(url)
    if asset_id is not None:
        return asset_llm_media_url(asset_id, absolute=True)
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if url.startswith("/media/"):
        return absolute_media_url(url)
    return url


def compress_image_for_llm(
    data: bytes,
    mime_type: str = "image/png",
    *,
    max_bytes: int | None = None,
    jpeg_quality: int | None = None,
    max_side_px: int | None = None,
) -> tuple[bytes, str]:
    """
    Уменьшить изображение для скачивания llama-server по HTTP.

    Если уже ≤ max_bytes — вернуть как есть. Иначе JPEG с понижением quality и стороны.
    """
    limit = max_bytes if max_bytes is not None else settings.llm_vision_max_bytes
    if len(data) <= limit:
        return data, mime_type

    quality = jpeg_quality if jpeg_quality is not None else settings.llm_vision_jpeg_quality
    max_side = max_side_px if max_side_px is not None else settings.llm_vision_max_side_px
    initial_quality = quality
    best = _encode_jpeg_for_llm(data, max_side=max_side, quality=quality)

    while len(best) > limit:
        if quality > 55:
            quality -= 5
            best = _encode_jpeg_for_llm(data, max_side=max_side, quality=quality)
            continue
        if max_side > 512:
            max_side = max(512, int(max_side * 0.75))
            quality = initial_quality
            best = _encode_jpeg_for_llm(data, max_side=max_side, quality=quality)
            continue
        if quality > 45:
            quality -= 5
            best = _encode_jpeg_for_llm(data, max_side=max_side, quality=quality)
            continue
        logger.warning(
            "LLM vision: не удалось уложиться в %d байт (получено %d)",
            limit,
            len(best),
        )
        break

    logger.info(
        "LLM vision: сжато %d → %d байт (сторона≤%d, q=%d)",
        len(data),
        len(best),
        max_side,
        quality,
    )
    return best, "image/jpeg"


def _encode_jpeg_for_llm(data: bytes, *, max_side: int, quality: int) -> bytes:
    with Image.open(io.BytesIO(data)) as img:
        img = _fit_image_max_side(img, max_side)
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        return buf.getvalue()


def _fit_image_max_side(img: Image.Image, max_side: int) -> Image.Image:
    w, h = img.size
    if max(w, h) <= max_side:
        return img
    resized = img.copy()
    resized.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    return resized


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


def resolve_trusted_generated_source(url_or_path: str) -> Path:
    """
    Безопасно разрешить путь к файлу в data/generated/.

    Допустимо:
    - имя файла (sd_….png);
    - /media/generated/{filename};
    - {PUBLIC_BASE_URL}/media/generated/{filename}.

    Raises:
        ValueError: Внешний или недопустимый источник.
        FileNotFoundError: Файл не найден.
    """
    raw = url_or_path.strip()
    if not raw:
        raise ValueError("Пустой URL или путь к изображению")

    base_url = settings.public_base_url.rstrip("/")

    if raw.startswith(base_url):
        suffix = raw[len(base_url) :]
        return _resolve_generated_media_suffix(suffix)

    if raw.startswith("/media/"):
        return _resolve_generated_media_suffix(raw)

    stripped = raw.strip("/")
    if "/" not in stripped and not stripped.startswith(("http://", "https://")):
        return resolve_generated_file(stripped, thumbs=False)

    raise ValueError(
        f"Недопустимый источник: {url_or_path}. "
        f"Разрешены только файлы из {base_url}/media/generated/… или имя файла."
    )


def _resolve_generated_media_suffix(suffix: str) -> Path:
    """Разобрать суффикс /media/generated/… или полный путь."""
    if suffix.startswith("/media/generated/thumbs/"):
        raise ValueError("Укажите полное изображение, не миниатюру")
    prefix = "/media/generated/"
    if not suffix.startswith(prefix):
        raise ValueError(f"Путь не из галереи generated: {suffix}")
    filename = Path(suffix[len(prefix) :]).name
    return resolve_generated_file(filename, thumbs=False)
