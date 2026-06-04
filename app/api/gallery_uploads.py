"""Галерея загрузок пользователя."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, Field
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.ws_events import broadcast_gallery_update
from app.db.repositories import MediaFavoriteRepository
from app.db.session import get_db
from app.integrations.media_utils import is_image_mime, sniff_image_mime
from app.services.gallery_owner import require_gallery_owner_user
from app.services.gallery_uploads_service import (
    delete_upload_asset,
    get_upload_item,
    list_upload_gallery,
    promote_disk_to_uploads,
    promote_generation_to_uploads,
    upload_to_gallery,
)
from app.services.request_user import RequestUser, get_request_user

_ROOT = Path(__file__).resolve().parents[2]
templates = Jinja2Templates(directory=str(_ROOT / "templates"))

router = APIRouter(tags=["gallery-uploads"])


class UploadFavoritePayload(BaseModel):
    id: str = Field(min_length=1)
    favorite: bool = True


@router.get("/gallery/uploads", response_class=HTMLResponse, include_in_schema=False)
async def uploads_gallery_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "gallery-uploads.html",
        {"title": "Загрузки"},
    )


@router.get("/api/gallery/uploads")
async def api_list_uploads(
    limit: int = 5000,
    db: AsyncSession = Depends(get_db),
    user: RequestUser | None = Depends(get_request_user),
) -> dict:
    items = await list_upload_gallery(db, request_user=user, limit=limit)
    return {
        "images": [i.to_api_dict() for i in items],
        "count": len(items),
    }


@router.get("/api/gallery/uploads/{asset_id}")
async def api_get_upload(
    asset_id: uuid.UUID,
    extract: bool = False,
    db: AsyncSession = Depends(get_db),
    user: RequestUser | None = Depends(get_request_user),
) -> dict:
    item = await get_upload_item(db, asset_id, request_user=user, extract=extract)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Не найдено")
    return item.to_api_dict()


@router.post("/api/gallery/uploads")
async def api_upload_files(
    files: list[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
    user: RequestUser | None = Depends(get_request_user),
) -> dict:
    if not files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Нет файлов")
    owner = await require_gallery_owner_user(db, user)
    created: list[dict] = []
    try:
        for uf in files:
            raw = await uf.read()
            if not raw:
                continue
            mime = uf.content_type or sniff_image_mime(raw) or "application/octet-stream"
            if not is_image_mime(mime):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Не изображение: {uf.filename or '?'}",
                )
            asset = await upload_to_gallery(
                db,
                request_user=user,
                data=raw,
                mime_type=mime,
                original_name=uf.filename,
            )
            created.append({"id": str(asset.id), "url": f"/media/asset/{asset.id}"})
        await db.commit()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if created:
        await broadcast_gallery_update(
            "created",
            kind="upload",
            count=len(created),
            user_id=owner.id,
        )
    return {"ok": True, "items": created, "count": len(created)}


@router.delete("/api/gallery/uploads/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def api_delete_upload(
    asset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: RequestUser | None = Depends(get_request_user),
) -> None:
    try:
        owner = await require_gallery_owner_user(db, user)
        await delete_upload_asset(db, asset_id, request_user=user)
        await db.commit()
        await broadcast_gallery_update(
            "deleted",
            kind="upload",
            asset_id=str(asset_id),
            user_id=owner.id,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Не найдено") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Доступ запрещён") from exc


@router.post("/api/gallery/disk/{filename}/promote-to-uploads")
async def api_promote_disk_to_uploads(
    filename: str,
    db: AsyncSession = Depends(get_db),
    user: RequestUser | None = Depends(get_request_user),
) -> dict:
    try:
        owner = await require_gallery_owner_user(db, user)
        asset = await promote_disk_to_uploads(db, filename, request_user=user)
        await db.commit()
        await broadcast_gallery_update(
            "promoted",
            kind="upload",
            asset_id=str(asset.id),
            user_id=owner.id,
        )
        return {"ok": True, "upload_id": str(asset.id), "url": f"/media/asset/{asset.id}"}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Не найдено") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Доступ запрещён") from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/api/gallery/{asset_id}/promote-to-uploads")
async def api_promote_to_uploads(
    asset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: RequestUser | None = Depends(get_request_user),
) -> dict:
    try:
        owner = await require_gallery_owner_user(db, user)
        asset = await promote_generation_to_uploads(db, asset_id, request_user=user)
        await db.commit()
        await broadcast_gallery_update(
            "promoted",
            kind="upload",
            asset_id=str(asset.id),
            user_id=owner.id,
        )
        return {"ok": True, "upload_id": str(asset.id), "url": f"/media/asset/{asset.id}"}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Не найдено") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Доступ запрещён") from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/api/gallery/uploads/favorite")
async def api_uploads_favorite(
    payload: UploadFavoritePayload,
    db: AsyncSession = Depends(get_db),
    user: RequestUser | None = Depends(get_request_user),
) -> dict:
    body = payload
    owner = await require_gallery_owner_user(db, user)
    repo = MediaFavoriteRepository(db)
    is_fav = await repo.set_favorite(
        source="db",
        media_id=body.id.strip(),
        is_favorite=body.favorite,
        user_id=owner.id,
    )
    await db.commit()
    await broadcast_gallery_update(
        "favorite",
        kind="upload",
        id=body.id,
        favorite=is_fav,
        user_id=owner.id,
    )
    return {"ok": True, "id": body.id, "is_favorite": is_fav}
