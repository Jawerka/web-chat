"""Доверенные IP внутренних сервисов."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.security.trusted_internal import (
    get_trusted_internal_ips,
    host_from_url,
    invalidate_trusted_internal_cache,
    is_trusted_internal_path,
    refresh_trusted_internal_from_settings,
    register_integration_urls,
    resolve_host_to_ips,
)


def test_host_from_url() -> None:
    assert host_from_url("http://192.168.88.41:8989/v1") == "192.168.88.41"


def test_resolve_literal_ip() -> None:
    assert "192.168.88.41" in resolve_host_to_ips("192.168.88.41")


def test_is_trusted_internal_path() -> None:
    assert is_trusted_internal_path("/media/asset/x/llm")
    assert is_trusted_internal_path("/api/health/logs")
    assert not is_trusted_internal_path("/api/conversations")


def test_register_integration_urls_adds_host(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.security import trusted_internal as ti

    monkeypatch.setattr(ti, "_dynamic_hosts", set())
    invalidate_trusted_internal_cache()
    register_integration_urls("http://10.20.30.40:8989/v1", None)
    assert "10.20.30.40" in ti._dynamic_hosts
    ips = get_trusted_internal_ips()
    assert "10.20.30.40" in ips


@pytest.mark.asyncio
async def test_media_llm_allowed_for_trusted_loopback(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.db import session as db_session
    from app.services.media_service import MediaService

    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "trusted_internal_allow_loopback", True)
    refresh_trusted_internal_from_settings()

    async with db_session.async_session_factory() as session:
        service = MediaService(session)
        asset = await service.create_from_bytes(b"\x89PNG\r\n\x1a\n", "image/png")
        await session.commit()
        asset_id = asset.id

    resp = await client.get(f"/media/asset/{asset_id}/llm")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_media_llm_denied_without_trust(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.db.session import dispose_database, init_db
    from app.main import create_app
    from tests.safety import safe_configure_database

    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "trusted_internal_allow_loopback", False)
    monkeypatch.setattr(settings, "llm_base_url", "http://192.168.99.99:8989/v1")
    monkeypatch.setattr(settings, "trusted_internal_ips", "")
    from app.security import trusted_internal as ti

    monkeypatch.setattr(ti, "_dynamic_hosts", set())
    invalidate_trusted_internal_cache()
    refresh_trusted_internal_from_settings()

    await dispose_database()
    db_url = f"sqlite+aiosqlite:///{tmp_path / 't.sqlite'}"
    safe_configure_database(db_url)
    await init_db()
    app = create_app()
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(f"/media/asset/{uuid.uuid4()}/llm")
        assert resp.status_code == 401

    await dispose_database()


@pytest.mark.asyncio
async def test_health_logs_via_trusted_loopback(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "trusted_internal_allow_loopback", True)
    refresh_trusted_internal_from_settings()
    response = await client.get("/api/health/logs?limit=50")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_sync_trusted_internal_endpoint(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "auth_enabled", False)
    resp = await client.post(
        "/api/config/trusted-internal/sync",
        json={"llm_base_url": "http://192.168.70.70:8989/v1", "sd_webui_url": None},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "192.168.70.70" in data.get("ui_hosts", [])
    assert data.get("ip_count", 0) >= 1
