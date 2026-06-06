"""
Раздача медиа: изображения из БД и legacy-файлы с диска.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.media_access import media_request_user
from app.security.trusted_internal import is_trusted_internal_request
from app.db.repositories import AttachmentRepository, MediaAssetRepository
from app.db.session import get_db
from app.integrations.media_utils import resolve_generated_file, resolve_upload_file
from app.services.media_service import MediaService
from app.services.request_user import RequestUser

router = APIRouter(prefix="/media", tags=["media"])


def _asset_cache_control() -> str:
    return "private, max-age=86400"


async def _serve_asset_bytes(
    service: MediaService,
    asset_id: uuid.UUID,
    *,
    request: Request,
    request_user: RequestUser | None,
    fetch,
) -> Response:
    trusted = is_trusted_internal_request(request)
    try:
        result = await fetch(
            asset_id,
            request_user=request_user,
            trusted_internal=trusted,
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Доступ запрещён",
        ) from exc
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Изображение не найдено")
    data, mime = result
    return Response(
        content=data,
        media_type=mime,
        headers={"Cache-Control": _asset_cache_control()},
    )


@router.get("/asset/{asset_id}")
async def serve_asset(
    asset_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    request_user: RequestUser | None = Depends(media_request_user),
) -> Response:
    """Изображение из БД (полный размер)."""
    service = MediaService(db)
    return await _serve_asset_bytes(
        service,
        asset_id,
        request=request,
        request_user=request_user,
        fetch=service.get_bytes,
    )


@router.get("/asset/{asset_id}/llm")
async def serve_asset_llm(
    asset_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    request_user: RequestUser | None = Depends(media_request_user),
) -> Response:
    """Изображение для LLM vision (JPEG ≤ llm_vision_max_bytes при необходимости)."""
    service = MediaService(db)
    return await _serve_asset_bytes(
        service,
        asset_id,
        request=request,
        request_user=request_user,
        fetch=service.get_llm_bytes,
    )


@router.get("/asset/{asset_id}/thumb")
async def serve_asset_thumb(
    asset_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    request_user: RequestUser | None = Depends(media_request_user),
) -> Response:
    """Миниатюра WebP из БД (или legacy JPEG в thumb_data)."""
    service = MediaService(db)
    return await _serve_asset_bytes(
        service,
        asset_id,
        request=request,
        request_user=request_user,
        fetch=service.get_thumb_bytes,
    )


@router.get("/asset/{asset_id}/preview")
async def serve_asset_preview(
    asset_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    request_user: RequestUser | None = Depends(media_request_user),
) -> Response:
    """Облегчённое WebP-превью для мобильных и плотных сеток."""
    service = MediaService(db)
    return await _serve_asset_bytes(
        service,
        asset_id,
        request=request,
        request_user=request_user,
        fetch=service.get_preview_bytes,
    )


@router.get("/uploads/{attachment_id}/{filename}", response_model=None)
async def serve_upload(
    attachment_id: uuid.UUID,
    filename: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    request_user: RequestUser | None = Depends(media_request_user),
):
    """Вложение: из БД (изображение) или с диска (документ)."""
    att_repo = AttachmentRepository(db)
    attachment = await att_repo.get_by_id(attachment_id)
    if attachment is not None and attachment.media_asset_id is not None:
        repo = MediaAssetRepository(db)
        asset = await repo.get_by_id(attachment.media_asset_id)
        if asset is not None:
            service = MediaService(db)
            try:
                result = await service.get_bytes(
                    asset.id,
                    request_user=request_user,
                    trusted_internal=is_trusted_internal_request(request),
                )
            except PermissionError as exc:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Доступ запрещён",
                ) from exc
            if result is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Изображение не найдено",
                )
            data, mime = result
            return Response(
                content=data,
                media_type=mime,
                headers={"Cache-Control": _asset_cache_control()},
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
