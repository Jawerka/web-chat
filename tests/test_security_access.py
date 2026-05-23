"""Тесты API key, rate limit, purge галереи."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.db.session import dispose_database, init_db
from tests.safety import assert_not_using_production_database, safe_configure_database
from app.main import create_app
from app.security.rate_limit import reset_rate_limits_for_tests
from tests.helpers import conversation_create_body, record_created_conversation


@pytest.fixture
async def secured_client(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Клиент с включённым API key и rate limit."""
    await dispose_database()
    monkeypatch.setattr(settings, "api_access_key", "test-secret-key")
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_requests", 3)
    monkeypatch.setattr(settings, "rate_limit_window_sec", 60)
    reset_rate_limits_for_tests()

    db_file = tmp_path / "sec.sec.sqlite"
    db_url = f"sqlite+aiosqlite:///{db_file}"
    safe_configure_database(db_url)
    await init_db()
    assert_not_using_production_database()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
        reset_rate_limits_for_tests()
    await dispose_database()


@pytest.mark.asyncio
async def test_api_requires_key_when_configured(secured_client: AsyncClient) -> None:
    r = await secured_client.get("/api/presets")
    assert r.status_code == 401

    r = await secured_client.get(
        "/api/presets",
        headers={"X-API-Key": "test-secret-key"},
    )
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_rate_limit_on_conversation_create(
    secured_client: AsyncClient,
    test_conv_title: str,
) -> None:
    headers = {"X-API-Key": "test-secret-key"}
    body = conversation_create_body(test_conv_title)
    for _ in range(3):
        r = await secured_client.post("/api/conversations", json=body, headers=headers)
        assert r.status_code in (200, 201)
        record_created_conversation(r.json())

    r = await secured_client.post("/api/conversations", json=body, headers=headers)
    assert r.status_code == 429
    body = r.json()
    assert body.get("code") == "rate_limit_error"


@pytest.mark.asyncio
async def test_purge_all_gallery_empty(client: AsyncClient) -> None:
    r = await client.delete("/api/gallery/all")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 0
