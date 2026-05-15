"""
REST API пресетов системных промптов.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import PresetOut
from app.db.repositories import PresetRepository
from app.db.session import get_db

router = APIRouter(prefix="/presets", tags=["presets"])


@router.get("", response_model=list[PresetOut])
async def list_presets(
    db: AsyncSession = Depends(get_db),
) -> list[PresetOut]:
    """Все пресеты."""
    repo = PresetRepository(db)
    presets = await repo.list_all()
    return [PresetOut.model_validate(p) for p in presets]


@router.post("/{preset_id}/set-default", response_model=PresetOut)
async def set_default_preset(
    preset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> PresetOut:
    """Установить пресет по умолчанию для новых бесед."""
    repo = PresetRepository(db)
    preset = await repo.set_default(preset_id)
    if preset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Пресет не найден",
        )
    return PresetOut.model_validate(preset)
