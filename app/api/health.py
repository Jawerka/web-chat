"""
Эндпоинт проверки живости и внешних зависимостей.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.config import settings
from app.public_url import public_base_url_lan, public_base_url_vpn, resolve_public_base_url
from app.integrations.llm_health import check_llm_available
from app.integrations.sd_health import check_sd_available

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str | bool]:
    """
    Проверка процесса, LLM и SD WebUI.

    status=degraded, если llm или sd недоступны, но процесс жив.
    """
    llm_status = await check_llm_available()
    sd_status = await check_sd_available()
    overall = "ok" if llm_status == "ok" and sd_status == "ok" else "degraded"
    timeouts_ok = settings.mcp_timeout > settings.request_timeout
    return {
        "status": overall,
        "llm": llm_status,
        "sd": sd_status,
        "public_base_url": resolve_public_base_url(),
        "public_base_url_lan": public_base_url_lan(),
        "public_base_url_vpn": public_base_url_vpn(),
        "timeouts_ok": timeouts_ok,
    }
