"""
Загрузка файлов пользователя (multipart).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import AttachmentOut, UploadResponse
from app.config import settings
from app.db.session import get_db
from app.services.attachment_service import AttachmentService, UploadValidationError

router = APIRouter(prefix="/upload", tags=["upload"])


@router.post("", response_model=UploadResponse)
async def upload_files(
    files: list[UploadFile] = File(..., description="Один или несколько файлов"),
    conversation_id: uuid.UUID | None = Form(None),
    db: AsyncSession = Depends(get_db),
) -> UploadResponse:
    """
    Загрузить файлы на сервер.

    Поле формы: files (несколько файлов с тем же именем).
    """
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Не переданы файлы",
        )
    if len(files) > settings.max_files_per_message:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Слишком много файлов (максимум {settings.max_files_per_message})",
        )

    service = AttachmentService(db)
    results: list[AttachmentOut] = []

    for upload in files:
        try:
            attachment = await service.register_upload(
                upload,
                conversation_id=conversation_id,
            )
        except UploadValidationError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail=exc.message,
            ) from exc

        results.append(
            AttachmentOut(
                id=attachment.id,
                original_name=attachment.original_name,
                mime_type=attachment.mime_type,
                size_bytes=attachment.size_bytes,
                preview_url=service.preview_url(attachment),
            )
        )

    return UploadResponse(attachments=results)
