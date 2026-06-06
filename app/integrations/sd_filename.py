"""
Имена файлов SD-изображений по шаблону refs/main.py:
MM-DD HH-MM [md5_5] - Seed.ext
"""

from __future__ import annotations

import base64
import hashlib
import io
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image, PngImagePlugin

from app.integrations.media_utils import safe_filename

_MSK_OFFSET_SEC = 3 * 3600
_GOOD_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def extract_seed_from_parameters(params_raw: str) -> str | None:
    """Seed из строки parameters (A1111), как в refs/main.py."""
    if not params_raw or "Seed: " not in params_raw:
        return None
    try:
        return params_raw.split("Seed: ")[1].split(", ")[0].strip()
    except IndexError:
        return None


def _image_extension(data: bytes, mime_type: str, fallback_name: str | None) -> str:
    if fallback_name:
        ext = Path(fallback_name).suffix.lower()
        if ext in _GOOD_EXTENSIONS:
            return ext
    try:
        with Image.open(io.BytesIO(data)) as im:
            fmt = (im.format or "").lower()
            if fmt in ("jpeg", "jpg"):
                return ".jpg"
            if fmt == "png":
                return ".png"
            if fmt == "webp":
                return ".webp"
    except Exception:
        pass
    if mime_type == "image/jpeg":
        return ".jpg"
    if mime_type == "image/webp":
        return ".webp"
    return ".png"


def _md5_short_from_pixels(data: bytes, *, length: int = 5) -> str:
    with Image.open(io.BytesIO(data)) as im:
        im.load()
        return hashlib.md5(im.tobytes()).hexdigest()[:length]


def _format_date_stamp(at: datetime) -> str:
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    ts = int(at.timestamp()) + _MSK_OFFSET_SEC
    return time.strftime("%m-%d %H-%M", time.gmtime(ts))


def sanitize_sd_filename(name: str, *, max_len: int = 255) -> str:
    """Допустимые символы для шаблона переименования (включая пробелы)."""
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-. ")
    result = "".join(c for c in name if c in allowed).strip()
    if not result:
        return "image.png"
    if len(result) <= max_len:
        return result
    stem, ext = Path(result).stem, Path(result).suffix
    if not ext:
        ext = ".png"
    keep = max_len - len(ext)
    if keep < 1:
        return result[:max_len]
    return f"{stem[:keep]}{ext}"


def build_sd_image_filename(
    data: bytes,
    *,
    mime_type: str,
    fallback_name: str | None = None,
    created_at: datetime | None = None,
    seed_override: str | int | None = None,
) -> str | None:
    """
    Сформировать имя по шаблону refs/main.py или None, если Seed недоступен.
    """
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            params_raw = (im.info.get("parameters") or "").strip()
            seed = extract_seed_from_parameters(params_raw)
            if not seed and seed_override is not None:
                seed = str(seed_override).strip()
            if not seed:
                return None
            ext = _image_extension(data, mime_type, fallback_name)
            short_hash = hashlib.md5(im.tobytes()).hexdigest()[:5]
    except Exception:
        return None

    at = created_at or datetime.now(UTC)
    formatted_date = _format_date_stamp(at)
    name = f"{formatted_date} {short_hash} - {seed}{ext}"
    return sanitize_sd_filename(name)


def resolve_upload_display_name(
    data: bytes,
    *,
    mime_type: str,
    fallback_name: str | None = None,
    created_at: datetime | None = None,
    seed_override: str | int | None = None,
) -> str:
    """Имя для original_name: SD-шаблон или безопасный fallback."""
    sd_name = build_sd_image_filename(
        data,
        mime_type=mime_type,
        fallback_name=fallback_name,
        created_at=created_at,
        seed_override=seed_override,
    )
    if sd_name:
        return sd_name
    if fallback_name:
        safe = safe_filename(Path(fallback_name).name)
        if safe:
            return safe
    ext = _image_extension(data, mime_type, fallback_name)
    return f"image{ext}"


def embed_png_metadata(
    image_bytes: bytes,
    *,
    parameters_text: str = "",
    seed: int | str | None = None,
    extra_text: dict[str, str] | None = None,
    description: str = "",
) -> bytes:
    """Встроить A1111-метаданные в PNG перед сохранением."""
    with Image.open(io.BytesIO(image_bytes)) as img:
        meta = PngImagePlugin.PngInfo()
        params = parameters_text.strip()
        if not params and seed is not None:
            params = f"Steps: 0, Seed: {seed}"
        if params:
            meta.add_text("parameters", params)
        for key, value in (extra_text or {}).items():
            if value:
                meta.add_text(key, value)
        if description:
            meta.add_text("Description", description)
        buf = io.BytesIO()
        img.save(buf, format="PNG", pnginfo=meta)
        return buf.getvalue()


def save_sd_generated_image(
    image: str | bytes,
    *,
    parameters_text: str = "",
    seed: int | str | None = None,
    extra_text: dict[str, str] | None = None,
    description: str = "",
    fallback_name: str | None = None,
) -> tuple[str, str | None]:
    """
    Сохранить SD-выход в data/generated/ с именем по шаблону refs/main.py.

    Returns:
        (имя файла на диске, имя миниатюры или None).
    """
    from app.integrations.media_utils import GENERATED_ROOT, make_thumbnail, save_image

    if isinstance(image, str):
        b64 = image.split(",", 1)[1] if "," in image else image
        raw = base64.b64decode(b64)
    else:
        raw = image

    final_data = embed_png_metadata(
        raw,
        parameters_text=parameters_text,
        seed=seed,
        extra_text=extra_text,
        description=description,
    )
    name = resolve_upload_display_name(
        final_data,
        mime_type="image/png",
        fallback_name=fallback_name,
        seed_override=seed,
    )
    path = GENERATED_ROOT / name
    if path.exists():
        stem, ext = Path(name).stem, Path(name).suffix or ".png"
        name = f"{stem}_{uuid.uuid4().hex[:4]}{ext}"
    save_image(final_data, name)
    thumb_name = make_thumbnail(name)
    return name, thumb_name
