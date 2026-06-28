"""
POST /api/conversations/from-image — новая беседа с изображением и текстом composer.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import ConversationFromImageCreate, ConversationFromImageOut
from app.db.session import get_db
from app.services.conversation_import_service import (
    ConversationImportError,
    create_conversation_from_image,
    image_source_from_json,
    image_source_from_multipart,
)
from app.services.request_user import RequestUser, get_request_user

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.post(
    "/from-image",
    response_model=ConversationFromImageOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_conversation_from_image_endpoint(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: RequestUser | None = Depends(get_request_user),
) -> ConversationFromImageOut:
    """
    Создать беседу с вложенным изображением и текстом для composer.

    JSON: { text, title?, preset_slug?, image: { asset_id | disk_filename | url } }
    multipart: text?, title?, preset_slug?, image (file)
    """
    content_type = (request.headers.get("content-type") or "").lower()

    try:
        if "multipart/form-data" in content_type:
            form = await request.form()
            upload = form.get("image")
            if upload is not None and not hasattr(upload, "read"):
                upload = None
            if upload is None:
                for _key, value in form.multi_items():
                    if hasattr(value, "read") and callable(value.read):
                        upload = value
                        break
            source = image_source_from_multipart(upload_file=upload)
            text_val = form.get("text")
            title_val = form.get("title")
            preset_val = form.get("preset_slug")
            return await create_conversation_from_image(
                db,
                source=source,
                text=str(text_val) if text_val is not None else None,
                title=str(title_val) if title_val is not None else None,
                preset_slug=str(preset_val) if preset_val is not None else "img2img",
                user=user,
            )

        body = ConversationFromImageCreate.model_validate(await request.json())
        source = image_source_from_json(body.image.model_dump(exclude_none=True))
        return await create_conversation_from_image(
            db,
            source=source,
            text=body.text,
            title=body.title,
            preset_slug=body.preset_slug,
            user=user,
        )
    except ConversationImportError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
