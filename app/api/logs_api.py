"""
API журнала приложения (кольцевой буфер в памяти).
"""

from __future__ import annotations

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field

from app.logging_buffer import append_client_log_lines, clear_log_buffer, get_log_lines

router = APIRouter(tags=["logs"])


class ClientLogIn(BaseModel):
    lines: list[str] = Field(default_factory=list, max_length=200)


@router.get("/logs")
async def list_logs(limit: int = Query(200, ge=1, le=500)) -> dict[str, list[str]]:
    """Последние строки серверного журнала."""
    return {"lines": get_log_lines(limit=limit)}


@router.post("/logs/client")
async def append_client_logs(body: ClientLogIn) -> dict[str, int]:
    """Принять строки клиентского журнала (браузер)."""
    added = append_client_log_lines(body.lines)
    return {"added": added}


@router.delete("/logs", status_code=status.HTTP_204_NO_CONTENT)
async def clear_logs() -> None:
    """Очистить серверный буфер журнала."""
    clear_log_buffer()
