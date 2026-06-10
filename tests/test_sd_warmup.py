"""Прогрев и готовность SD checkpoint."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.integrations.sd_warmup import (
    invalidate_sd_ready_cache,
    mark_sd_ready,
    parse_sd_error_body,
    sd_ready_cached,
)


def test_parse_sd_error_body_json() -> None:
    class FakeResp:
        status_code = 500
        text = ""

        @staticmethod
        def json() -> dict:
            return {"errors": "cuda/cpu mismatch"}

    assert "cuda" in parse_sd_error_body(FakeResp())


def test_sd_ready_cache_ttl() -> None:
    invalidate_sd_ready_cache()
    mark_sd_ready("http://sd.test", "model.safetensors", ttl_sec=60.0)
    assert sd_ready_cached("http://sd.test", "model.safetensors") is True
    assert sd_ready_cached("http://sd.test", "other.safetensors") is False
    invalidate_sd_ready_cache("http://sd.test")
    assert sd_ready_cached("http://sd.test", "model.safetensors") is False


@pytest.mark.asyncio
async def test_sd_ready_endpoint_cached(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _selected(*_args: object, **_kwargs: object) -> str:
        return "cached.ckpt"

    monkeypatch.setattr(
        "app.api.config_api._fetch_sd_selected_checkpoint",
        _selected,
    )
    mark_sd_ready("http://sd.test", "cached.ckpt")
    response = await client.get(
        "/api/config/sd-ready",
        params={"sd_webui_url": "http://sd.test"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ready"] is True
    invalidate_sd_ready_cache("http://sd.test")
