"""Публичные настройки для UI (без секретов)."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.config import settings

router = APIRouter(prefix="/config", tags=["config"])


class PublicConfigOut(BaseModel):
    """Лимиты и базовый URL для фронтенда."""

    max_upload_mb: int
    max_files_per_message: int
    public_base_url: str


@router.get("", response_model=PublicConfigOut)
async def get_public_config() -> PublicConfigOut:
    """GET /api/config — лимиты загрузки и public_base_url."""
    return PublicConfigOut(
        max_upload_mb=settings.max_upload_mb,
        max_files_per_message=settings.max_files_per_message,
        public_base_url=settings.public_base_url,
    )
