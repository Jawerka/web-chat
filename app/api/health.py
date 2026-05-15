"""
Эндпоинт проверки живости и внешних зависимостей.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.integrations.llm_health import check_llm_available
from app.integrations.sd_health import check_sd_available

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """
    Проверка процесса, LLM и SD WebUI.

    status=degraded, если llm или sd недоступны, но процесс жив.
    """
    llm_status = await check_llm_available()
    sd_status = await check_sd_available()
    overall = "ok" if llm_status == "ok" and sd_status == "ok" else "degraded"
    return {
        "status": overall,
        "llm": llm_status,
        "sd": sd_status,
    }
