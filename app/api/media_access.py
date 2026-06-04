"""Зависимость: пользователь запроса для раздачи /media/asset/*."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.services.auth_service import request_user_from_model, resolve_authenticated_user
from app.services.request_user import RequestUser, resolve_request_user_from_header


async def media_request_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> RequestUser | None:
    """Сессия при AUTH_ENABLED; иначе legacy X-Web-Chat-User или None."""
    if settings.auth_enabled:
        user = await resolve_authenticated_user(db, request)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Требуется вход",
            )
        return request_user_from_model(user)
    try:
        return await resolve_request_user_from_header(
            db,
            user_slug=request.headers.get("x-web-chat-user"),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
