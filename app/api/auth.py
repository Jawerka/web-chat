"""
REST: вход, выход, текущий пользователь.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.security.access import client_ip_from_request
from app.services.auth_service import (
    AuthUserView,
    authenticate_login,
    change_password,
    clear_session_cookie,
    resolve_authenticated_user,
    set_session_cookie,
)
from app.services.request_user import RequestUser, get_request_user

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginBody(BaseModel):
    login: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class UserOut(BaseModel):
    id: str
    login: str
    slug: str
    display_name: str
    role: str


class ChangePasswordBody(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=4, max_length=256)


@router.post("/login", response_model=UserOut)
async def login(
    body: LoginBody,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> UserOut:
    """Вход по логину и паролю; установка HttpOnly-cookie сессии."""
    if not settings.auth_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Аутентификация отключена",
        )
    user = await authenticate_login(
        db,
        login=body.login,
        password=body.password,
        client_ip=client_ip_from_request(request),
    )
    await db.commit()
    set_session_cookie(response, user.id)
    view = AuthUserView.from_model(user)
    return UserOut(
        id=str(view.id),
        login=view.login,
        slug=view.slug,
        display_name=view.display_name,
        role=view.role,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> None:
    """Выйти: удалить cookie сессии."""
    clear_session_cookie(response)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def auth_change_password(
    body: ChangePasswordBody,
    db: AsyncSession = Depends(get_db),
    user: RequestUser = Depends(get_request_user),
) -> None:
    """Сменить пароль текущей сессии (без сброса cookie)."""
    if not settings.auth_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Аутентификация отключена",
        )
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется вход",
        )
    await change_password(
        db,
        user_id=user.id,
        current_password=body.current_password,
        new_password=body.new_password,
    )
    await db.commit()


@router.get("/me", response_model=UserOut)
async def auth_me(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> UserOut:
    """Текущий пользователь по сессии."""
    if not settings.auth_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Аутентификация отключена",
        )
    user = await resolve_authenticated_user(db, request)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется вход",
        )
    view = AuthUserView.from_model(user)
    return UserOut(
        id=str(view.id),
        login=view.login,
        slug=view.slug,
        display_name=view.display_name,
        role=view.role,
    )
