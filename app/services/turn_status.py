"""
Канонические фазы хода (turn) для content_json — P0.4.

Схема: queued → streaming → tool_running → partial → completed | cancelled | failed
"""

from __future__ import annotations

from typing import Any

# Активные фазы (streaming=True)
STREAMING = "streaming"
TOOL_RUNNING = "tool_running"
QUEUED = "queued"

# Финальные
COMPLETED = "completed"
CANCELLED = "cancelled"
FAILED = "failed"
PARTIAL = "partial"

_ALL = frozenset(
    {
        QUEUED,
        STREAMING,
        TOOL_RUNNING,
        PARTIAL,
        COMPLETED,
        CANCELLED,
        FAILED,
    }
)


def status_code_to_turn_phase(status_code: str) -> str:
    """Маппинг legacy turn_status / error code → turn_phase."""
    if status_code == "cancelled":
        return CANCELLED
    if status_code in ("completed", "ok"):
        return COMPLETED
    if status_code in ("failed", "llm_error", "tool_loop", "internal", "validation"):
        return FAILED
    return PARTIAL


def patch_active_turn_phase(
    payload: dict[str, Any],
    *,
    turn_phase: str,
    legacy_phase: str | None = None,
) -> dict[str, Any]:
    """Обновить turn_phase и опционально legacy phase (text/tool)."""
    merged = dict(payload)
    merged["turn_phase"] = turn_phase
    if legacy_phase is not None:
        merged["phase"] = legacy_phase
    return merged


def patch_completed(payload: dict[str, Any]) -> dict[str, Any]:
    """Успешное завершение хода."""
    merged = dict(payload)
    merged["turn_phase"] = COMPLETED
    merged["streaming"] = None
    merged["phase"] = None
    merged["active_tool"] = None
    merged.pop("turn_status", None)
    merged.pop("turn_status_message", None)
    return merged


def patch_interrupted(
    payload: dict[str, Any],
    *,
    status_code: str,
    status_message: str | None = None,
) -> dict[str, Any]:
    """Прерванный ход (ошибка / отмена) с видимым контентом."""
    merged = dict(payload)
    merged["streaming"] = None
    merged["phase"] = None
    merged["active_tool"] = None
    merged["turn_status"] = status_code
    merged["turn_phase"] = status_code_to_turn_phase(status_code)
    if status_message:
        merged["turn_status_message"] = status_message
    elif "turn_status_message" in merged:
        del merged["turn_status_message"]
    return merged
