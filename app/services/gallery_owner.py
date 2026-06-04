"""
Владелец галереи для запроса: сессия, legacy-заголовок или bootstrap admin.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import GalleryKind, MediaAsset, User
from app.db.repositories import UserRepository
from app.security.media_encryption import generate_media_token
from app.services.request_user import RequestUser


async def ensure_user_media_token(user: User) -> None:
    """Сгенерировать media_token, если ещё нет."""
    if user.media_token is not None and len(user.media_token) >= 16:
        return
    from datetime import UTC, datetime

    user.media_token = generate_media_token()
    user.media_token_created_at = datetime.now(UTC)


async def resolve_gallery_owner_id(
    session: AsyncSession,
    request_user: RequestUser | None,
) -> uuid.UUID | None:
    """
    UUID владельца для фильтра галереи.

    None — без фильтра (однопользовательский режим без auth/header).
    """
    if request_user is not None:
        user = await UserRepository(session).get_by_id(request_user.id)
        if user is not None:
            await ensure_user_media_token(user)
            await session.flush()
            return user.id

    if settings.effective_multi_user:
        return None

    return None


async def require_gallery_owner_user(
    session: AsyncSession,
    request_user: RequestUser | None,
) -> User:
    """Пользователь с media_token для uploads API (создаёт legacy default при необходимости)."""
    repo = UserRepository(session)
    if request_user is not None:
        user = await repo.get_by_id(request_user.id)
        if user is None:
            raise ValueError("user not found")
        await ensure_user_media_token(user)
        await session.flush()
        return user

    if settings.auth_enabled:
        raise ValueError("auth required")

    from app.security.passwords import LEGACY_HEADER_PASSWORD_HASH

    user = await repo.get_or_create_legacy_header_user(
        slug="default",
        display_name="default",
        password_hash=LEGACY_HEADER_PASSWORD_HASH,
    )
    await ensure_user_media_token(user)
    await session.flush()
    return user


async def assert_gallery_media_access(
    session: AsyncSession,
    asset: MediaAsset,
    request_user: RequestUser | None,
) -> None:
    """
    Запрет чтения BLOB чужого пользователя (403).

    Legacy без owner_user_id и однопользовательский режим — без проверки.
    """
    if asset.owner_user_id is None:
        return
    if not settings.auth_enabled and not settings.effective_multi_user:
        return
    if request_user is None and asset.gallery_kind == GalleryKind.CHAT.value:
        return
    owner_id = await resolve_gallery_owner_id(session, request_user)
    if owner_id is None or asset.owner_user_id != owner_id:
        raise PermissionError("forbidden")


async def bootstrap_admin_id(session: AsyncSession) -> uuid.UUID | None:
    """ID admin для backfill миграций."""
    if not settings.auth_enabled:
        login = settings.auth_bootstrap_admin_login.strip().lower()
        admin = await UserRepository(session).get_by_login(login)
        if admin is not None:
            return admin.id
        return None
    from app.services.auth_service import ensure_bootstrap_admin

    admin = await ensure_bootstrap_admin(session)
    await ensure_user_media_token(admin)
    return admin.id
