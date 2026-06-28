"""
Сборка A1111 infotext из SdMetadata / полей БД.
"""

from __future__ import annotations

from app.services.sd_metadata import SdMetadata, extract_sd_metadata_from_bytes


def build_infotext_from_fields(
    *,
    prompt: str = "",
    negative: str = "",
    params: str = "",
) -> str:
    """Собрать строку parameters для paste в SD WebUI."""
    prompt = (prompt or "").strip()
    negative = (negative or "").strip()
    params = (params or "").strip()

    parts: list[str] = []
    if prompt:
        parts.append(prompt)
    if negative:
        parts.append(f"Negative prompt: {negative}")
    if params:
        parts.append(params)
    return "\n".join(parts).strip()


def build_a1111_infotext(meta: SdMetadata) -> str:
    return build_infotext_from_fields(
        prompt=meta.prompt,
        negative=meta.negative,
        params=meta.params,
    )


def infotext_from_png_bytes(data: bytes) -> str | None:
    """Infotext из chunk parameters PNG/WebP."""
    meta = extract_sd_metadata_from_bytes(data)
    if meta is None or not meta.has_metadata:
        return None
    text = build_a1111_infotext(meta)
    return text or None
