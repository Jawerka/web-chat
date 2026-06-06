"""
Понятные пользователю статусы хода (этап + подпись + процент).

Согласованы с WS-событием ``progress`` и полями ``generation-status``.
"""

from __future__ import annotations

from typing import Any

# Этапы (machine-readable)
STAGE_SUBMIT = "submit"
STAGE_LLM_THINKING = "llm_thinking"
STAGE_LLM_TYPING = "llm_typing"
STAGE_LLM_TOOLS = "llm_tools"
STAGE_SD_RENDER = "sd_render"
STAGE_SD_UPSCALE = "sd_upscale"
STAGE_DOC_READ = "doc_read"
STAGE_GALLERY = "gallery"
STAGE_SAVE_MEDIA = "save_media"

_SD_TOOLS = frozenset({"generate_image", "img2img", "upscale_images"})

_TOOL_STAGE: dict[str, str] = {
    "generate_image": STAGE_SD_RENDER,
    "img2img": STAGE_SD_RENDER,
    "upscale_images": STAGE_SD_UPSCALE,
    "extract_text": STAGE_DOC_READ,
    "get_gallery": STAGE_GALLERY,
}

_STAGE_LABEL: dict[str, str] = {
    STAGE_SUBMIT: "Сообщение принято",
    STAGE_LLM_THINKING: "Размышление",
    STAGE_LLM_TYPING: "Печатаю ответ",
    STAGE_LLM_TOOLS: "Выбираю действие",
    STAGE_SD_RENDER: "Рисую изображение",
    STAGE_SD_UPSCALE: "Увеличиваю",
    STAGE_DOC_READ: "Читаю документ",
    STAGE_GALLERY: "Галерея",
    STAGE_SAVE_MEDIA: "Сохраняю",
}


def stage_for_tool(tool_name: str) -> str:
    """Этап UI по имени инструмента."""
    return _TOOL_STAGE.get(tool_name, STAGE_LLM_TOOLS)


def is_sd_tool(tool_name: str) -> bool:
    return tool_name in _SD_TOOLS


def build_progress(
    stage: str,
    *,
    tool: str | None = None,
    percent: int | None = None,
    detail: str | None = None,
    label: str | None = None,
) -> dict[str, Any]:
    """
    Payload для WS ``progress`` / кэша в ConnectionManager.

    ``percent``: 0–100 или None (неопределённый прогресс — анимация без цифры).
    """
    resolved_label = label or _label_for(stage, tool=tool, percent=percent)
    payload: dict[str, Any] = {
        "stage": stage,
        "label": resolved_label,
        "detail": (detail or "").strip(),
        "tool": tool,
    }
    if percent is not None:
        payload["percent"] = max(0, min(100, int(percent)))
    return payload


def _label_for(stage: str, *, tool: str | None, percent: int | None) -> str:
    base = _STAGE_LABEL.get(stage, "Выполняется")
    if stage == STAGE_SD_RENDER and tool == "img2img":
        base = "Дорабатываю изображение"
    elif stage == STAGE_SD_RENDER and tool == "generate_image":
        base = "Рисую изображение"
    if percent is not None and stage in (STAGE_SD_RENDER, STAGE_SD_UPSCALE):
        return f"{base} — {percent}%"
    return base


def progress_from_sd_snapshot(
    tool_name: str,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Собрать progress из ответа ``/sdapi/v1/progress``."""
    stage = stage_for_tool(tool_name)
    percent = snapshot.get("percent")
    detail = snapshot.get("detail") or ""
    payload = build_progress(
        stage,
        tool=tool_name,
        percent=percent,
        detail=detail,
    )
    preview = snapshot.get("preview")
    if isinstance(preview, str) and preview.startswith("data:image/"):
        payload["preview"] = preview
    return payload
