"""Тесты API журнала."""

from __future__ import annotations

from httpx import AsyncClient

from app.logging_buffer import clear_log_buffer, get_log_lines


async def test_logs_list_and_clear(client: AsyncClient) -> None:
    clear_log_buffer()
    assert get_log_lines() == []

    r = await client.get("/api/logs")
    assert r.status_code == 200
    assert "lines" in r.json()

    d = await client.delete("/api/logs")
    assert d.status_code == 204
