"""
API журнала приложения (кольцевой буфер в памяти).
"""

from __future__ import annotations

from fastapi import APIRouter, Query, status

from app.logging_buffer import clear_log_buffer, get_log_lines

router = APIRouter(tags=["logs"])


@router.get("/logs")
async def list_logs(limit: int = Query(200, ge=1, le=500)) -> dict[str, list[str]]:
    """Последние строки серверного журнала."""
    return {"lines": get_log_lines(limit=limit)}


@router.delete("/logs", status_code=status.HTTP_204_NO_CONTENT)
async def clear_logs() -> None:
    """Очистить серверный буфер журнала."""
    clear_log_buffer()
