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
    limit: int = Query(4000, ge=50, le=10000),
    since_hours: float | None = Query(
        None,
        ge=0.1,
        le=168,
        description="Только строки за последние N часов",
    ),
) -> dict:
    """Объединённый журнал backend + frontend."""
    ensure_log_buffer_attached()
    return collect_aggregate_logs(
        buffer_limit=limit,
        file_tail=limit,
        client_limit=limit,
        since_hours=since_hours,
    )
