"""
Управление пользователями (admin-only, P2.2).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import UserRole
from app.db.repositories import UserRepository
from app.db.session import get_db
from app.security.passwords import hash_password
from app.services.request_user import RequestUser, require_admin

router = APIRouter(prefix="/users", tags=["users"])

_LOGIN_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")


class UserListItem(BaseModel):
    id: str
    login: str
    slug: str
    display_name: str
    role: str
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None


class CreateUserBody(BaseModel):
    login: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=4, max_length=256)
    display_name: str | None = Field(default=None, max_length=120)
    role: Literal["admin", "user"] = "user"


def _user_list_item(user) -> UserListItem:
    return UserListItem(
        id=str(user.id),
        login=user.login,
        slug=user.slug,
        display_name=user.display_name,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
    )


def _normalize_login(raw: str) -> str:
    login = raw.strip().lower()
    if not _LOGIN_RE.fullmatch(login):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Логин: a-z, 0-9, _, -; от 2 до 64 символов",
        )
    return login


@router.get("", response_model=list[UserListItem])
async def list_users(
    db: AsyncSession = Depends(get_db),
    _admin: RequestUser = Depends(require_admin),
) -> list[UserListItem]:
    """Список пользователей (только admin)."""
    if not settings.auth_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Аутентификация отключена",
        )
    users = await UserRepository(db).list_all()
    return [_user_list_item(u) for u in users]


@router.post("", response_model=UserListItem, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: CreateUserBody,
    db: AsyncSession = Depends(get_db),
    _admin: RequestUser = Depends(require_admin),
) -> UserListItem:
    """Создать пользователя (только admin)."""
    if not settings.auth_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Аутентификация отключена",
        )
    login = _normalize_login(body.login)
    role = UserRole.ADMIN if body.role == "admin" else UserRole.USER
    display = (body.display_name or login).strip() or login
    repo = UserRepository(db)
    try:
        user = await repo.create_user(
            login=login,
            password_hash=hash_password(body.password),
            display_name=display,
            role=role,
        )
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Пользователь с таким логином уже существует",
        ) from exc
    return _user_list_item(user)
