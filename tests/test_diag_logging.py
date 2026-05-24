"""Тесты диагностического логирования и буфера."""

from __future__ import annotations

import logging

import pytest
from httpx import AsyncClient

from app.diag_logging import redact_url, summarize_llm_messages
from app.logging_buffer import clear_log_buffer, ensure_log_buffer_attached, get_log_lines


def test_redact_url_asset() -> None:
    u = "http://192.168.1.1:8090/media/asset/550e8400-e29b-41d4-a716-446655440000/llm"
    assert "550e8400" not in redact_url(u)
    assert "/media/asset/" in redact_url(u)


def test_summarize_llm_messages_multimodal() -> None:
    summary = summarize_llm_messages(
        [
            {"role": "user", "content": [{"type": "text", "text": "hi"}, {"type": "image_url", "image_url": {"url": "/media/x"}}]},
        ],
    )
    assert summary["image_parts"] == 1
    assert summary["message_count"] == 1


def test_ensure_log_buffer_attached(monkeypatch: pytest.MonkeyPatch) -> None:
    import logging as log_mod

    import app.logging_buffer as lb

    monkeypatch.setattr(lb, "_HANDLER", None)
    lb.install_log_buffer()
    ensure_log_buffer_attached()
    assert lb._HANDLER is not None
    assert lb._HANDLER in log_mod.getLogger().handlers


@pytest.mark.asyncio
async def test_health_logs_trusted_loopback(client: AsyncClient, monkeypatch) -> None:
    from app.config import settings
    from app.security.trusted_internal import refresh_trusted_internal_from_settings

    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "trusted_internal_allow_loopback", True)
    refresh_trusted_internal_from_settings()
    response = await client.get("/api/health/logs?limit=50")
    assert response.status_code == 200
    assert "lines" in response.json()
