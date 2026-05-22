"""Тесты resolve_model и кэша LLM."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.integrations.llm_client import LLMClient, _MODEL_CACHE


@pytest.fixture(autouse=True)
def clear_model_cache() -> None:
    _MODEL_CACHE.clear()
    yield
    _MODEL_CACHE.clear()


@pytest.mark.asyncio
async def test_fetch_first_model_id_retries_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.integrations.llm_client.settings.llm_model_load_retry_sec",
        0.01,
    )
    monkeypatch.setattr(
        "app.integrations.llm_client.settings.llm_model_load_wait_sec",
        5,
    )
    responses = [
        httpx.Response(503, text='{"error":{"message":"Loading model"}}'),
        httpx.Response(200, json={"data": [{"id": "qwen-test"}]}),
    ]

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=responses)

    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("app.integrations.llm_client.httpx.AsyncClient", return_value=mock_cm):
        client = LLMClient(base_url="http://llm.test/v1")
        model = await client._fetch_first_model_id()

    assert model == "qwen-test"
    assert mock_client.get.await_count == 2


@pytest.mark.asyncio
async def test_resolve_model_uses_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.integrations.llm_client.settings.llm_model", "")
    fetch = AsyncMock(return_value="cached-model")
    monkeypatch.setattr(LLMClient, "_fetch_first_model_id", fetch)
    base = "http://llm.test/v1"
    c1 = LLMClient(base_url=base)
    c2 = LLMClient(base_url=base)
    assert await c1.resolve_model() == "cached-model"
    assert await c2.resolve_model() == "cached-model"
    fetch.assert_awaited_once()
