"""
Live preview из SD WebUI ``/sdapi/v1/progress`` (поле ``current_image``).
"""

from __future__ import annotations

from app.config import settings


def preview_data_url_from_b64(raw_b64: str | None) -> str | None:
    """
    Собрать data:-URL для UI.

    WebUI отдаёт base64 без префикса (обычно JPEG, иногда PNG).
    """
    if not settings.sd_preview_enabled:
        return None
    if not raw_b64 or not isinstance(raw_b64, str):
        return None
    b64 = raw_b64.strip()
    if not b64:
        return None
    if len(b64) > settings.sd_preview_max_b64_chars:
        return None
    mime = "image/jpeg"
    if b64.startswith("iVBOR"):
        mime = "image/png"
    return f"data:{mime};base64,{b64}"
