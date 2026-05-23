"""
Текущий пользователь запроса (сессия или legacy-заголовок).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Request, WebSocket, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.repositories import UserRepository
from app.db.session import get_db
from app.security.passwords import LEGACY_HEADER_PASSWORD_HASH
from app.services.auth_service import request_user_from_model, resolve_authenticated_user


@dataclass(frozen=True, slots=True)
class RequestUser:
    """Идентификатор пользователя в рамках HTTP/WS запроса."""

    id: uuid.UUID
    slug: str
    display_name: str
    login: str = ""
    role: str = "user"

_USER_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _normalize_user_slug(raw: str | None) -> str:
    slug = (raw or "default").strip().lower()
    if not slug or not _USER_SLUG_RE.fullmatch(slug):
        raise ValueError(
            "Некорректный X-Web-Chat-User (a-z, 0-9, _, -, до 64 символов)",
        )
    return slug


async def resolve_request_user_from_header(
    db: AsyncSession,
    *,
    user_slug: str | None,
) -> RequestUser | None:
    """Legacy: пользователь из X-Web-Chat-User (только без сессий)."""
    if settings.auth_enabled and not settings.multi_user_allow_header_fallback:
        return None
    if not settings.effective_multi_user:
        return None
    slug = _normalize_user_slug(user_slug)
    user = await UserRepository(db).get_or_create_legacy_header_user(
        slug=slug,
        display_name=slug,
        password_hash=LEGACY_HEADER_PASSWORD_HASH,
    )
    return request_user_from_model(user)


async def resolve_request_user_from_websocket(
    websocket: WebSocket,
    db: AsyncSession,
) -> RequestUser | None:
    """Пользователь WebSocket: сессия (cookie) или legacy-заголовок."""
    if settings.auth_enabled:
        token = websocket.cookies.get("webchat_session")
        if token:
            from app.security.session_tokens import load_session_token

            user_id = load_session_token(
                token,
                secret=settings.auth_secret,
                max_age_sec=settings.auth_session_max_age_sec,
            )
            if user_id:
                user = await UserRepository(db).get_by_id(user_id)
                if user is not None and user.is_active:
                    return request_user_from_model(user)
        if not settings.multi_user_allow_header_fallback:
            return None
    return await resolve_request_user_from_header(
        db,
        user_slug=websocket.headers.get("x-web-chat-user"),
    )


async def get_request_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_web_chat_user: str | None = Header(default=None, alias="X-Web-Chat-User"),
) -> RequestUser | None:
    """
    Зависимость FastAPI: сессия (при AUTH_ENABLED) или заголовок (legacy).
    """
    if settings.auth_enabled:
        user = await resolve_authenticated_user(db, request)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Требуется вход",
            )
        return request_user_from_model(user)

    try:
        return await resolve_request_user_from_header(db, user_slug=x_web_chat_user)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


def owner_user_id_for_request(user: RequestUser | None) -> uuid.UUID | None:
    """UUID владельца для фильтра репозитория или None (без изоляции)."""
    if settings.effective_multi_user:
        return user.id if user is not None else None
    return None


async def require_admin(
    user: RequestUser | None = Depends(get_request_user),
) -> RequestUser:
    """Только активный admin (при включённой аутентификации)."""
    if not settings.auth_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Аутентификация отключена",
        )
    if user is None or user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Требуются права администратора",
        )
    return user
