"""
Раздача медиа: изображения из БД и legacy-файлы с диска.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories import AttachmentRepository, MediaAssetRepository
from app.db.session import get_db
from app.integrations.media_utils import resolve_generated_file, resolve_upload_file
from app.services.media_service import MediaService

router = APIRouter(prefix="/media", tags=["media"])


@router.get("/asset/{asset_id}")
async def serve_asset(
    asset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Изображение из БД (полный размер)."""
    service = MediaService(db)
    result = await service.get_bytes(asset_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Изображение не найдено")
    data, mime = result
    return Response(
        content=data,
        media_type=mime,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/asset/{asset_id}/llm")
async def serve_asset_llm(
    asset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Изображение для LLM vision (JPEG ≤ llm_vision_max_bytes при необходимости)."""
    service = MediaService(db)
    result = await service.get_llm_bytes(asset_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Изображение не найдено")
    data, mime = result
    return Response(
        content=data,
        media_type=mime,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/asset/{asset_id}/thumb")
async def serve_asset_thumb(
    asset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Миниатюра WebP из БД (или legacy JPEG в thumb_data)."""
    service = MediaService(db)
    result = await service.get_thumb_bytes(asset_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Изображение не найдено")
    data, mime = result
    return Response(
        content=data,
        media_type=mime,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/asset/{asset_id}/preview")
async def serve_asset_preview(
    asset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Облегчённое WebP-превью для мобильных и плотных сеток."""
    service = MediaService(db)
    result = await service.get_preview_bytes(asset_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Изображение не найдено")
    data, mime = result
    return Response(
        content=data,
        media_type=mime,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/uploads/{attachment_id}/{filename}", response_model=None)
async def serve_upload(
    attachment_id: uuid.UUID,
    filename: str,
    db: AsyncSession = Depends(get_db),
):
    """Вложение: из БД (изображение) или с диска (документ)."""
    att_repo = AttachmentRepository(db)
    attachment = await att_repo.get_by_id(attachment_id)
    if attachment is not None and attachment.media_asset_id is not None:
        repo = MediaAssetRepository(db)
        asset = await repo.get_by_id(attachment.media_asset_id)
        if asset is not None:
            return Response(
                content=asset.data,
                media_type=asset.mime_type,
                headers={"Cache-Control": "public, max-age=86400"},
            )

    try:
        path = resolve_upload_file(attachment_id, filename)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Файл не найден",
        ) from exc

    return FileResponse(path)


@router.get("/generated/{filename}")
async def serve_generated(filename: str) -> FileResponse:
    """Legacy: сгенерированное SD изображение с диска."""
    try:
        path = resolve_generated_file(filename, thumbs=False)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Файл не найден",
        ) from exc
    return FileResponse(path, headers={"Cache-Control": "public, max-age=3600"})


@router.get("/generated/thumbs/{filename}")
async def serve_generated_thumb(filename: str) -> FileResponse:
    """Legacy: миниатюра с диска."""
    try:
        path = resolve_generated_file(filename, thumbs=True)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Миниатюра не найдена",
        ) from exc
    return FileResponse(path, headers={"Cache-Control": "public, max-age=3600"})
