"""Тесты эндпоинта health."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.services.health_service import (
    HealthHistoryPoint,
    HealthReport,
    ServiceProbe,
    _merge_backend_lines,
)


def _sample_report(*, overall: str = "ok", llm: str = "ok", sd: str = "ok") -> HealthReport:
    return HealthReport(
        status=overall,  # type: ignore[arg-type]
        generated_at=1_700_000_000.0,
        uptime_sec=120.0,
        llm=llm,
        sd=sd,
        public_base_url="http://test",
        public_base_url_lan="http://test",
        public_base_url_vpn=None,
        timeouts_ok=True,
        llm_model_configured="test-model",
        services=[
            ServiceProbe(
                id="llm",
                name="LLM",
                status="ok" if llm == "ok" else "unavailable",
                latency_ms=10,
            ),
            ServiceProbe(
                id="sd",
                name="SD",
                status="ok" if sd == "ok" else "unavailable",
                latency_ms=20,
            ),
        ],
        history=[
            HealthHistoryPoint(
                ts=1_700_000_000.0,
                overall=100,
                llm=100,
                sd=100,
                database=100,
            ),
        ],
        active_generations=0,
        disk_free_mb=1024.0,
        generated_count=3,
    )


@pytest.mark.asyncio
async def test_health_returns_structure(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /api/health возвращает расширенный JSON."""

    async def _report() -> HealthReport:
        return _sample_report()

    monkeypatch.setattr("app.api.health.build_health_report", _report)
    response = await client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["llm"] == "ok"
    assert data["sd"] == "ok"
    assert "public_base_url" in data
    assert data["timeouts_ok"] is True
    assert "services" in data
    assert "history" in data
    assert "disk_free_mb" in data
    assert "generated_count" in data


@pytest.mark.asyncio
async def test_health_dashboard_html(
    client: AsyncClient,
) -> None:
    """GET /health — HTML-дашборд."""
    response = await client.get("/health")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert "Состояние сервисов" in response.text
    assert "/static/js/health.js" in response.text


@pytest.mark.asyncio
async def test_health_degraded_when_sd_down(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """status=degraded при недоступном SD."""

    async def _report() -> HealthReport:
        return _sample_report(overall="degraded", sd="unavailable")

    monkeypatch.setattr("app.api.health.build_health_report", _report)
    response = await client.get("/api/health")
    assert response.json()["status"] == "degraded"
    assert response.json()["sd"] == "unavailable"


def test_merge_backend_lines_dedupes_buffer_and_file() -> None:
  """Буфер и файл с одной строкой → одна запись в журнале."""
  line = (
      "2026-06-09 21:47:01,212 INFO [app.security.trusted_internal] "
      "conv=- turn=- ws=- trusted_internal: 6 IP"
  )
  merged = _merge_backend_lines([line], [line])
  assert merged == [line]


@pytest.mark.asyncio
async def test_health_logs_endpoint(client: AsyncClient) -> None:
    """GET /api/health/logs — объединённый журнал."""
    response = await client.get("/api/health/logs?limit=100")
    assert response.status_code == 200
    data = response.json()
    assert "lines" in data
    assert "line_count" in data
