"""
Текущий пользователь запроса (P2.2 multi-user).

При ``MULTI_USER_ENABLED=false`` возвращается ``None`` — поведение как single-tenant.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, WebSocket, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.repositories import UserRepository
from app.db.session import get_db

_USER_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


@dataclass(frozen=True, slots=True)
class RequestUser:
    """Идентификатор пользователя в рамках HTTP/WS запроса."""

    id: uuid.UUID
    slug: str
    display_name: str


def _normalize_user_slug(raw: str | None) -> str:
    slug = (raw or "default").strip().lower()
    if not slug or not _USER_SLUG_RE.fullmatch(slug):
        raise ValueError(
            "Некорректный X-Web-Chat-User (a-z, 0-9, _, -, до 64 символов)",
        )
    return slug


async def resolve_request_user(
    db: AsyncSession,
    *,
    user_slug: str | None,
) -> RequestUser | None:
    """Разрешить пользователя по slug или None, если multi-user выключен."""
    if not settings.multi_user_enabled:
        return None
    slug = _normalize_user_slug(user_slug)
    user = await UserRepository(db).get_or_create(slug=slug, display_name=slug)
    return RequestUser(id=user.id, slug=user.slug, display_name=user.display_name)


async def resolve_request_user_from_websocket(
    websocket: WebSocket,
    db: AsyncSession,
) -> RequestUser | None:
    """Пользователь из заголовков WebSocket upgrade."""
    return await resolve_request_user(
        db,
        user_slug=websocket.headers.get("x-web-chat-user"),
    )


async def get_request_user(
    db: AsyncSession = Depends(get_db),
    x_web_chat_user: str | None = Header(default=None, alias="X-Web-Chat-User"),
) -> RequestUser | None:
    """
    Зависимость FastAPI: пользователь из заголовка ``X-Web-Chat-User``.

    Без ``MULTI_USER_ENABLED`` — ``None``.
    """
    try:
        return await resolve_request_user(db, user_slug=x_web_chat_user)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


def owner_user_id_for_request(user: RequestUser | None) -> uuid.UUID | None:
    """UUID владельца для фильтра репозитория или None (без изоляции)."""
    return user.id if user is not None else None
