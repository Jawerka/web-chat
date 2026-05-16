"""Тесты эндпоинта health."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_returns_structure(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /api/health возвращает status и sd."""

    async def _ok() -> str:
        return "ok"

    monkeypatch.setattr("app.api.health.check_llm_available", _ok)
    monkeypatch.setattr("app.api.health.check_sd_available", _ok)
    response = await client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["llm"] == "ok"
    assert data["sd"] == "ok"
    assert "public_base_url" in data
    assert data["timeouts_ok"] is True


@pytest.mark.asyncio
async def test_health_degraded_when_sd_down(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """status=degraded при недоступном SD."""

    async def _llm_ok() -> str:
        return "ok"

    async def _sd_down() -> str:
        return "unavailable"

    monkeypatch.setattr("app.api.health.check_llm_available", _llm_ok)
    monkeypatch.setattr("app.api.health.check_sd_available", _sd_down)
    response = await client.get("/api/health")
    assert response.json()["status"] == "degraded"
    assert response.json()["sd"] == "unavailable"
