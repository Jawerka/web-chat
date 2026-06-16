"""Тесты публичного API конфигурации."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_public_config_includes_llm_model(client: AsyncClient) -> None:
    """GET /api/config возвращает llm_model."""
    response = await client.get("/api/config")
    assert response.status_code == 200
    data = response.json()
    assert "llm_model" in data
    assert "llm_base_url" in data
    assert "sd_webui_url" in data
    assert "max_upload_mb" in data
    assert "wd_tagger_enabled" in data


@pytest.mark.asyncio
async def test_llm_model_endpoint(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /api/config/llm-model — resolved модель."""

    async def _resolve(self, override: str | None = None) -> str:
        return "test-model-v1"

    monkeypatch.setattr(
        "app.api.config_api.LLMClient.resolve_model",
        _resolve,
    )
    response = await client.get("/api/config/llm-model")
    assert response.status_code == 200
    data = response.json()
    assert data["resolved"] == "test-model-v1"
    assert data["source"] in ("config", "auto")


@pytest.mark.asyncio
async def test_llm_warmup_endpoint(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /api/config/llm-warmup — короткий прогрев LLM."""

    async def _resolve(self, override: str | None = None) -> str:
        return "warm-model"

    async def _plain(self, messages, **kwargs: object) -> str:
        return ""

    monkeypatch.setattr("app.api.config_api.LLMClient.resolve_model", _resolve)
    monkeypatch.setattr("app.api.config_api.LLMClient.complete_plain_text", _plain)

    response = await client.post("/api/config/llm-warmup", json={})
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["model"] == "warm-model"
