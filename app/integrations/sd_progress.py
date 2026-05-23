"""
Опрос прогресса Stable Diffusion WebUI (``/sdapi/v1/progress``).
"""

from __future__ import annotations

import logging
from typing import Any

import requests

from app.integrations.runtime_config import resolve_sd_webui_url
from app.integrations.sd_tools import get_sd_session

logger = logging.getLogger(__name__)


def fetch_sd_progress(sd_webui_url: str | None = None) -> dict[str, Any] | None:
    """
    Снимок прогресса SD (синхронно, для job queue / thread).

    Returns:
        dict с percent, detail, active — или None если SD недоступен.
    """
    base = resolve_sd_webui_url(sd_webui_url)
    session = get_sd_session()
    try:
        resp = session.get(
            f"{base}/sdapi/v1/progress",
            timeout=5,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.debug("SD progress: %s", exc)
        return None

    data = resp.json()
    progress = float(data.get("progress") or 0.0)
    state = data.get("state") if isinstance(data.get("state"), dict) else {}
    sampling_step = int(state.get("sampling_step") or 0)
    sampling_steps = int(state.get("sampling_steps") or 0)
    job_no = int(state.get("job_no") or 0)
    job_count = int(state.get("job_count") or 0)

    active = job_count > 0 or progress > 0.01
    if not active:
        return {"active": False, "percent": 0, "detail": ""}

    percent = max(0, min(100, int(round(progress * 100))))
    detail_parts: list[str] = []
    if sampling_steps > 0:
        detail_parts.append(f"шаг {sampling_step}/{sampling_steps}")
    elif job_count > 0:
        detail_parts.append(f"задача {job_no + 1}/{job_count}")
    textinfo = (data.get("textinfo") or "").strip()
    if textinfo:
        detail_parts.append(textinfo[:120])
    eta = data.get("eta_relative")
    if isinstance(eta, (int, float)) and eta > 0:
        detail_parts.append(f"осталось ~{int(eta)} с")

    return {
        "active": True,
        "percent": percent,
        "detail": " · ".join(detail_parts),
        "progress_raw": progress,
        "sampling_step": sampling_step,
        "sampling_steps": sampling_steps,
    }
