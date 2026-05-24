"""
Проверка живости: JSON API и данные для дашборда.
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.logging_buffer import ensure_log_buffer_attached
from app.services.health_service import build_health_report, collect_aggregate_logs

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> JSONResponse:
    """
    Расширенный статус для мониторинга и UI.

    Совместимость: поля status, llm, sd, public_base_url*, timeouts_ok.
    """
    report = await build_health_report()
    payload = report.model_dump()
    return JSONResponse(content=payload)


@router.get("/health/logs")
async def health_logs(
    limit: int = Query(500, ge=50, le=2000),
) -> dict:
    """Объединённый журнал web-chat (память + файл)."""
    ensure_log_buffer_attached()
    data = collect_aggregate_logs(buffer_limit=limit, file_tail=limit)
    return data
