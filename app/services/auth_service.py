"""
Аутентификация: логин, сессии, bootstrap admin (P2.2).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from fastapi import HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import User, UserRole
from app.db.repositories import ConversationRepository, UserRepository
from app.security.passwords import hash_password, verify_password
from app.security.rate_limit import RateLimitExceeded, check_rate_limit
from app.security.session_tokens import SESSION_COOKIE_NAME, create_session_token
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AuthUserView:
    id: uuid.UUID
    login: str
    slug: str
    display_name: str
    role: str

    @classmethod
    def from_model(cls, user: User) -> AuthUserView:
        return cls(
            id=user.id,
            login=user.login,
            slug=user.slug,
            display_name=user.display_name,
            role=user.role,
        )


def request_user_from_model(user: User):
    from app.services.request_user import RequestUser

    return RequestUser(
        id=user.id,
        slug=user.slug,
        display_name=user.display_name,
            role=user.role,
        login=user.login,
    )


def session_user_id_from_request(request: Request) -> uuid.UUID | None:
    if not settings.auth_enabled:
        return None
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    from app.security.session_tokens import load_session_token

    return load_session_token(
        token,
        secret=settings.auth_secret,
        max_age_sec=settings.auth_session_max_age_sec,
    )


def set_session_cookie(response: Response, user_id: uuid.UUID) -> None:
    token = create_session_token(user_id=user_id, secret=settings.auth_secret)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=settings.auth_session_max_age_sec,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
    )


async def authenticate_login(
    db: AsyncSession,
    *,
    login: str,
    password: str,
    client_ip: str,
) -> User:
    """Проверить учётные данные; обновить last_login_at."""
    try:
        check_rate_limit(f"auth-login:{client_ip}")
    except RateLimitExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "rate_limit_error", "message": str(exc)},
        ) from exc

    repo = UserRepository(db)
    user = await repo.get_by_login(login)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный логин или пароль",
        )
    if not verify_password(password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный логин или пароль",
        )
    await repo.touch_last_login(user)
    return user


async def change_password(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    current_password: str,
    new_password: str,
) -> None:
    """Сменить пароль текущего пользователя (требуется верный текущий пароль)."""
    repo = UserRepository(db)
    user = await repo.get_by_id(user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется вход",
        )
    if not verify_password(current_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Неверный текущий пароль",
        )
    if current_password == new_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Новый пароль должен отличаться от текущего",
        )
    await repo.update_password_hash(user, hash_password(new_password))


async def resolve_authenticated_user(
    db: AsyncSession,
    request: Request,
) -> User | None:
    """Текущий пользователь из сессии."""
    if not settings.auth_enabled:
        return None
    user_id = session_user_id_from_request(request)
    if user_id is None:
        return None
    user = await UserRepository(db).get_by_id(user_id)
    if user is None or not user.is_active:
        return None
    return user


async def ensure_bootstrap_admin(db: AsyncSession) -> User:
    """
    Создать admin при первом старте и привязать orphan-беседы.

    Пароль из AUTH_BOOTSTRAP_ADMIN_PASSWORD (сменить после установки).
    """
    repo = UserRepository(db)
    login = settings.auth_bootstrap_admin_login.strip().lower()
    admin = await repo.get_by_login(login)
    if admin is None:
        admin = await repo.create_user(
            login=login,
            slug=login,
            display_name="Administrator",
            password_hash=hash_password(settings.auth_bootstrap_admin_password),
            role=UserRole.ADMIN,
        )
        logger.warning(
            "Создан учётная запись admin (%s). Смените пароль после первого входа.",
            login,
        )
    from app.services.gallery_owner import ensure_user_media_token

    await ensure_user_media_token(admin)
    assigned = await ConversationRepository(db).assign_orphan_conversations(admin.id)
    if assigned:
        logger.info(
            "Назначено %d бесед пользователю %r",
            assigned,
            login,
        )
    return admin
