"""P4.5: disk_free_mb и generated_count в /api/health."""

from __future__ import annotations

from pathlib import Path
import pytest
from httpx import AsyncClient

from app.services.health_service import (
    HealthReport,
    ServiceProbe,
    _generated_disk_count,
    disk_free_mb,
)


def test_generated_disk_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gen = tmp_path / "generated"
    gen.mkdir()
    (gen / "a.png").write_bytes(b"x")
    (gen / "b.txt").write_bytes(b"y")
    (gen / "c.webp").write_bytes(b"z")
    monkeypatch.setattr("app.services.health_service.GENERATED_ROOT", gen)
    assert _generated_disk_count() == 2


def test_disk_free_mb_positive() -> None:
    assert disk_free_mb() > 0


@pytest.mark.asyncio
async def test_health_json_includes_ops_fields(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _report() -> HealthReport:
        return HealthReport(
            status="ok",
            generated_at=1.0,
            uptime_sec=1.0,
            llm="ok",
            sd="ok",
            public_base_url="http://test",
            public_base_url_lan="http://test",
            public_base_url_vpn=None,
            timeouts_ok=True,
            llm_model_configured="m",
            services=[
                ServiceProbe(id="app", name="app", status="ok", latency_ms=0),
            ],
            history=[],
            active_generations=0,
            disk_free_mb=512.5,
            generated_count=7,
        )

    monkeypatch.setattr("app.api.health.build_health_report", _report)
    data = (await client.get("/api/health")).json()
    assert data["disk_free_mb"] == 512.5
    assert data["generated_count"] == 7
