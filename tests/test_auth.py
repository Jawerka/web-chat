"""Аутентификация: login, сессия, изоляция."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.config import settings
from app.security.passwords import hash_password
from app.security.session_tokens import SESSION_COOKIE_NAME


@pytest.fixture
def auth_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "auth_secret", "test-auth-secret-key-32chars-minimum!!")
    monkeypatch.setattr(settings, "multi_user_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_enabled", False)


@pytest.mark.asyncio
async def test_login_sets_session_cookie(
    client: AsyncClient,
    auth_settings: None,
) -> None:
    from app.db import session as db_session
    from app.db.models import UserRole
    from app.db.repositories import UserRepository

    async with db_session.async_session_factory() as session:
        await UserRepository(session).create_user(
            login="tester",
            slug="tester",
            display_name="Tester",
            password_hash=hash_password("secret"),
            role=UserRole.USER,
        )
        await session.commit()

    res = await client.post(
        "/api/auth/login",
        json={"login": "tester", "password": "secret"},
    )
    assert res.status_code == 200
    assert SESSION_COOKIE_NAME in res.cookies
    assert res.json()["login"] == "tester"

    me = await client.get("/api/auth/me")
    assert me.status_code == 200


@pytest.mark.asyncio
async def test_protected_api_without_session(
    client: AsyncClient,
    auth_settings: None,
) -> None:
    res = await client.get("/api/conversations")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_bootstrap_admin_assigns_conversations(
    client: AsyncClient,
    auth_settings: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.db import session as db_session
    from app.db.repositories import ConversationRepository, PresetRepository, UserRepository
    from app.services.auth_service import ensure_bootstrap_admin

    async with db_session.async_session_factory() as session:
        preset = await PresetRepository(session).get_default()
        assert preset is not None
        await ConversationRepository(session).create(
            title="[pytest] for admin",
            preset_id=preset.id,
            owner_user_id=None,
        )
        await session.commit()

    monkeypatch.setattr(settings, "auth_bootstrap_admin_password", "admin")
    async with db_session.async_session_factory() as session:
        admin = await ensure_bootstrap_admin(session)
        await session.commit()
        assert admin.login == "admin"

    login = await client.post(
        "/api/auth/login",
        json={"login": "admin", "password": "admin"},
    )
    assert login.status_code == 200
    convs = await client.get("/api/conversations")
    assert convs.status_code == 200
    assert len(convs.json()) >= 1


@pytest.mark.asyncio
async def test_admin_lists_and_creates_users(
    client: AsyncClient,
    auth_settings: None,
) -> None:
    from app.db import session as db_session
    from app.db.models import UserRole
    from app.db.repositories import UserRepository

    from app.security.passwords import hash_password

    async with db_session.async_session_factory() as session:
        await UserRepository(session).create_user(
            login="admin2",
            slug="admin2",
            display_name="Admin",
            password_hash=hash_password("adminpass"),
            role=UserRole.ADMIN,
        )
        await session.commit()

    login = await client.post(
        "/api/auth/login",
        json={"login": "admin2", "password": "adminpass"},
    )
    assert login.status_code == 200

    listed = await client.get("/api/users")
    assert listed.status_code == 200
    logins = {u["login"] for u in listed.json()}
    assert "admin2" in logins

    created = await client.post(
        "/api/users",
        json={"login": "newbie", "password": "secret123", "role": "user"},
    )
    assert created.status_code == 201
    assert created.json()["login"] == "newbie"

    forbidden = await client.post(
        "/api/auth/login",
        json={"login": "newbie", "password": "secret123"},
    )
    assert forbidden.status_code == 200

    as_user = await client.get("/api/users")
    assert as_user.status_code == 403


@pytest.mark.asyncio
async def test_change_password_updates_login(
    client: AsyncClient,
    auth_settings: None,
) -> None:
    from app.db import session as db_session
    from app.db.models import UserRole
    from app.db.repositories import UserRepository

    async with db_session.async_session_factory() as session:
        await UserRepository(session).create_user(
            login="pwuser",
            slug="pwuser",
            display_name="PW",
            password_hash=hash_password("old-secret"),
            role=UserRole.USER,
        )
        await session.commit()

    login = await client.post(
        "/api/auth/login",
        json={"login": "pwuser", "password": "old-secret"},
    )
    assert login.status_code == 200

    bad = await client.post(
        "/api/auth/change-password",
        json={"current_password": "wrong", "new_password": "new-secret"},
    )
    assert bad.status_code == 400

    ok = await client.post(
        "/api/auth/change-password",
        json={"current_password": "old-secret", "new_password": "new-secret"},
    )
    assert ok.status_code == 204

    old_login = await client.post(
        "/api/auth/login",
        json={"login": "pwuser", "password": "old-secret"},
    )
    assert old_login.status_code == 401

    await client.post("/api/auth/logout")
    new_login = await client.post(
        "/api/auth/login",
        json={"login": "pwuser", "password": "new-secret"},
    )
    assert new_login.status_code == 200


@pytest.mark.asyncio
async def test_public_config_includes_auth_enabled(
    client: AsyncClient,
    auth_settings: None,
) -> None:
    res = await client.get("/api/config")
    assert res.status_code == 200
    assert res.json()["auth_enabled"] is True
