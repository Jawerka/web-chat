"""
Сбор метрик и проверок для дашборда /health.
"""

from __future__ import annotations

import logging
import shutil
import socket
import time
from pathlib import Path
from collections import deque
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.api.ws_manager import manager
from app.config import settings
from app.db import session as db_session
from app.logging_buffer import get_log_lines

logger = logging.getLogger(__name__)

ServiceStatus = Literal["ok", "degraded", "loading", "unavailable"]


class ServiceProbe(BaseModel):
    """Один проверяемый компонент."""

    id: str
    name: str
    status: ServiceStatus
    latency_ms: float | None = None
    detail: str = ""
    url: str = ""
    load_percent: int | None = Field(
        None,
        description="0–100 для полоски загрузки (латентность или прогресс)",
    )
    extra: dict[str, Any] = Field(default_factory=dict)


class HealthHistoryPoint(BaseModel):
    """Точка для мини-графика доступности."""

    ts: float
    overall: int
    llm: int
    sd: int
    database: int


class HealthReport(BaseModel):
    """Полный отчёт для JSON и UI."""

    status: Literal["ok", "degraded"]
    generated_at: float
    uptime_sec: float
    llm: str
    sd: str
    public_base_url: str
    public_base_url_lan: str
    public_base_url_vpn: str | None
    timeouts_ok: bool
    llm_model_configured: str
    services: list[ServiceProbe]
    history: list[HealthHistoryPoint]
    active_generations: int


_HISTORY: deque[HealthHistoryPoint] = deque(maxlen=90)
_APP_STARTED = time.monotonic()


def _score(status: ServiceStatus) -> int:
    if status == "ok":
        return 100
    if status == "loading":
        return 45
    if status == "degraded":
        return 55
    return 0


def _latency_load(ms: float | None, *, good: float = 400, warn: float = 2000) -> int | None:
    if ms is None:
        return None
    if ms <= good:
        return max(8, int(100 - (ms / good) * 40))
    if ms <= warn:
        return int(60 + ((ms - good) / (warn - good)) * 30)
    return min(98, int(90 + (ms - warn) / 100))


async def _probe_llm() -> ServiceProbe:
    configured = settings.llm_model or ""
    base = settings.llm_base_url.rstrip("/")
    url = f"{base}/models"
    headers: dict[str, str] = {}
    if settings.llm_api_key:
        headers["Authorization"] = f"Bearer {settings.llm_api_key}"
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            response = await client.get(url, headers=headers or None)
        latency = (time.perf_counter() - t0) * 1000
        body = response.text[:300]
        if response.status_code == 503 or "loading" in body.lower():
            return ServiceProbe(
                id="llm",
                name="LLM (чат)",
                status="loading",
                latency_ms=round(latency, 1),
                detail="Модель загружается (HTTP 503)",
                url=settings.llm_base_url,
                load_percent=72,
                extra={"http_status": response.status_code},
            )
        if not response.is_success:
            return ServiceProbe(
                id="llm",
                name="LLM (чат)",
                status="unavailable",
                latency_ms=round(latency, 1),
                detail=f"HTTP {response.status_code}",
                url=settings.llm_base_url,
                load_percent=_latency_load(latency),
                extra={"http_status": response.status_code},
            )
        data = response.json()
        models = data.get("data") if isinstance(data, dict) else None
        if models is None and isinstance(data, dict):
            models = data.get("models")
        model_ids: list[str] = []
        if isinstance(models, list):
            for m in models[:5]:
                if isinstance(m, dict):
                    model_ids.append(str(m.get("id") or m.get("name") or ""))
                else:
                    model_ids.append(str(m))
        primary = model_ids[0] if model_ids else ""
        detail = configured or primary or "API доступен"
        if configured and primary and primary != configured:
            detail = f"{configured} (в API: {primary})"
        return ServiceProbe(
            id="llm",
            name="LLM (чат)",
            status="ok",
            latency_ms=round(latency, 1),
            detail=detail,
            url=settings.llm_base_url,
            load_percent=_latency_load(latency),
            extra={
                "models": model_ids,
                "configured_model": configured,
            },
        )
    except httpx.HTTPError as exc:
        latency = (time.perf_counter() - t0) * 1000
        return ServiceProbe(
            id="llm",
            name="LLM (чат)",
            status="unavailable",
            latency_ms=round(latency, 1),
            detail=str(exc),
            url=settings.llm_base_url,
            load_percent=0,
        )


async def _probe_sd() -> ServiceProbe:
    base = settings.sd_webui_url.rstrip("/")
    url = f"{base}/sdapi/v1/sd-models"
    auth = None
    if settings.sd_auth_user and settings.sd_auth_pass:
        auth = (settings.sd_auth_user, settings.sd_auth_pass)
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            response = await client.get(url, auth=auth)
        latency = (time.perf_counter() - t0) * 1000
        if not response.is_success:
            return ServiceProbe(
                id="sd",
                name="Stable Diffusion",
                status="unavailable",
                latency_ms=round(latency, 1),
                detail=f"HTTP {response.status_code}",
                url=base,
                load_percent=_latency_load(latency),
            )
        models = response.json()
        count = len(models) if isinstance(models, list) else 0
        title = ""
        if count and isinstance(models[0], dict):
            title = str(models[0].get("title") or models[0].get("model_name") or "")[:80]
        progress_detail = ""
        progress_load: int | None = _latency_load(latency)
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                prog = await client.get(f"{base}/sdapi/v1/progress", auth=auth)
            if prog.is_success:
                pdata = prog.json()
                pct = float(pdata.get("progress") or 0) * 100
                if pdata.get("state", {}).get("job_count", 0) or pct > 0.5:
                    progress_load = min(99, int(pct))
                    progress_detail = f"Генерация: {pct:.0f}%"
        except httpx.HTTPError:
            pass
        detail = progress_detail or (title or f"{count} checkpoint(ов)")
        return ServiceProbe(
            id="sd",
            name="Stable Diffusion",
            status="ok",
            latency_ms=round(latency, 1),
            detail=detail,
            url=base,
            load_percent=progress_load,
            extra={"checkpoints": count},
        )
    except httpx.HTTPError as exc:
        latency = (time.perf_counter() - t0) * 1000
        return ServiceProbe(
            id="sd",
            name="Stable Diffusion",
            status="unavailable",
            latency_ms=round(latency, 1),
            detail=str(exc),
            url=base,
            load_percent=0,
        )


def _db_extra() -> dict[str, Any]:
    """Метаданные БД для health (SQLite WAL / размер Postgres)."""
    from app.db.url import is_postgres_url, is_sqlite_url

    if is_postgres_url():
        return _postgres_db_extra()
    if is_sqlite_url():
        return _sqlite_db_extra()
    return {}


def _postgres_db_extra() -> dict[str, Any]:
    extra: dict[str, Any] = {"backend": "postgresql"}
    try:
        from app.db.pg_cli import pg_connection_params

        p = pg_connection_params()
        extra["db_host"] = p["host"]
        extra["db_name"] = p["database"]
    except Exception:
        pass
    return extra


def _sqlite_db_extra() -> dict[str, Any]:
    """Размер WAL и счётчик retry busy (P1.1)."""
    from pathlib import Path

    from app.db.sqlite import sqlite_busy_retries_total

    extra: dict[str, Any] = {
        "backend": "sqlite",
        "sqlite_busy_retries": sqlite_busy_retries_total(),
    }
    url = settings.database_url
    if "sqlite" in url:
        part = url.split("///", 1)[-1]
        path = Path(part[2:]) if part.startswith("./") else Path(part)
        if path.is_file():
            extra["db_size_mb"] = round(path.stat().st_size / (1024 * 1024), 2)
        wal = path.with_suffix(path.suffix + "-wal")
        if wal.is_file():
            extra["wal_size_mb"] = round(wal.stat().st_size / (1024 * 1024), 2)
    return extra


async def _probe_database() -> ServiceProbe:
    from app.db.url import is_postgres_url

    t0 = time.perf_counter()
    try:
        async with db_session.async_session_factory() as session:
            await session.execute(text("SELECT 1"))
            db_size: str | None = None
            if is_postgres_url():
                row = await session.execute(
                    text("SELECT pg_size_pretty(pg_database_size(current_database()))"),
                )
                db_size = row.scalar()
        latency = (time.perf_counter() - t0) * 1000
        extra = _db_extra()
        if is_postgres_url():
            detail_parts = ["PostgreSQL"]
            if db_size:
                detail_parts.append(db_size)
                extra["db_size"] = db_size
        else:
            detail_parts = ["SQLite"]
            if extra.get("wal_size_mb") is not None:
                detail_parts.append(f"WAL {extra['wal_size_mb']} MB")
            if extra.get("sqlite_busy_retries"):
                detail_parts.append(f"busy retries: {extra['sqlite_busy_retries']}")
        return ServiceProbe(
            id="database",
            name="База данных",
            status="ok",
            latency_ms=round(latency, 1),
            detail=", ".join(detail_parts),
            url="local",
            load_percent=_latency_load(latency, good=50, warn=500),
            extra=extra,
        )
    except Exception as exc:
        latency = (time.perf_counter() - t0) * 1000
        return ServiceProbe(
            id="database",
            name="База данных",
            status="unavailable",
            latency_ms=round(latency, 1),
            detail=str(exc),
            url="local",
            load_percent=0,
        )


def _probe_mcp() -> ServiceProbe:
    port = settings.effective_mcp_port
    host = settings.web_host if settings.web_host not in ("0.0.0.0", "::") else "127.0.0.1"
    t0 = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=2.0):
            pass
        latency = (time.perf_counter() - t0) * 1000
        return ServiceProbe(
            id="mcp",
            name="MCP (tools)",
            status="ok",
            latency_ms=round(latency, 1),
            detail=f"Порт {port}",
            url=f"http://{host}:{port}",
            load_percent=_latency_load(latency, good=80, warn=400),
        )
    except OSError as exc:
        latency = (time.perf_counter() - t0) * 1000
        return ServiceProbe(
            id="mcp",
            name="MCP (tools)",
            status="unavailable",
            latency_ms=round(latency, 1),
            detail=str(exc),
            url=f":{port}",
            load_percent=0,
        )


def _data_disk_extra() -> dict[str, Any]:
    """Свободное место на томе с каталогом data/ (P1.6)."""
    data_dir = Path("data")
    root = data_dir if data_dir.is_dir() else Path(".")
    usage = shutil.disk_usage(root)
    free_gb = round(usage.free / (1024**3), 2)
    used_pct = round(100 * usage.used / usage.total, 1) if usage.total else 0
    return {"data_free_gb": free_gb, "data_disk_used_percent": used_pct}


def _probe_app() -> ServiceProbe:
    busy = manager.active_turn_count()
    ws_count = manager.websocket_count()
    disk = _data_disk_extra()
    detail = (
        f"Порт {settings.web_port}, генераций: {busy}, WS: {ws_count}, "
        f"свободно {disk['data_free_gb']} GB"
    )
    return ServiceProbe(
        id="app",
        name="web-chat",
        status="ok",
        latency_ms=0,
        detail=detail,
        url=settings.public_base_url,
        load_percent=min(100, busy * 25) if busy else 12,
        extra={
            "web_port": settings.web_port,
            "active_turns": busy,
            "ws_connections": ws_count,
            **disk,
        },
    )


async def build_health_report() -> HealthReport:
    """Собрать полный отчёт и обновить историю для графиков."""
    from app.public_url import public_base_url_lan, public_base_url_vpn, resolve_public_base_url

    services = [
        _probe_app(),
        await _probe_llm(),
        await _probe_sd(),
        await _probe_database(),
        _probe_mcp(),
    ]
    llm_probe = next(s for s in services if s.id == "llm")
    sd_probe = next(s for s in services if s.id == "sd")
    db_probe = next(s for s in services if s.id == "database")

    llm_legacy = "ok" if llm_probe.status in ("ok", "loading") else "unavailable"
    if llm_probe.status == "loading":
        llm_legacy = "unavailable"
    sd_legacy = "ok" if sd_probe.status == "ok" else "unavailable"

    critical = [llm_probe, sd_probe, db_probe]
    overall: Literal["ok", "degraded"] = "ok"
    if any(s.status == "unavailable" for s in critical):
        overall = "degraded"
    elif any(s.status in ("loading", "degraded") for s in critical):
        overall = "degraded"

    point = HealthHistoryPoint(
        ts=time.time(),
        overall=_score("ok" if overall == "ok" else "degraded"),
        llm=_score(llm_probe.status),
        sd=_score(sd_probe.status),
        database=_score(db_probe.status),
    )
    _HISTORY.append(point)

    return HealthReport(
        status=overall,
        generated_at=time.time(),
        uptime_sec=round(time.monotonic() - _APP_STARTED, 1),
        llm=llm_legacy,
        sd=sd_legacy,
        public_base_url=resolve_public_base_url(),
        public_base_url_lan=public_base_url_lan(),
        public_base_url_vpn=public_base_url_vpn(),
        timeouts_ok=settings.mcp_timeout > settings.request_timeout,
        llm_model_configured=settings.llm_model or "",
        services=services,
        history=list(_HISTORY),
        active_generations=len(manager.busy_conversation_ids()),
    )


def collect_aggregate_logs(*, buffer_limit: int = 400, file_tail: int = 400) -> dict[str, Any]:
    """Объединённый журнал: буфер в памяти + хвост файла."""
    sections: list[dict[str, Any]] = []
    lines: list[str] = []
    _skip_fragments = ("http://llm.test/", "cached-model", "greenlet_spawn has not been called")

    mem = get_log_lines(limit=buffer_limit)
    if mem:
        filtered = [ln for ln in mem if not any(s in ln for s in _skip_fragments)]
        if filtered:
            sections.append({"source": "web-chat (память)", "count": len(filtered)})
            lines.append("════ web-chat · буфер приложения ════")
            lines.extend(filtered)

    log_path = settings.log_file.strip()
    if log_path:
        path = __import__("pathlib").Path(log_path)
        if path.is_file():
            try:
                raw = path.read_text(encoding="utf-8", errors="replace").splitlines()
                tail = raw[-file_tail:] if len(raw) > file_tail else raw
                filtered = [
                    ln
                    for ln in tail
                    if not any(s in ln for s in _skip_fragments)
                    and "Exception closing connection" not in ln
                    and "MissingGreenlet" not in ln
                ]
                if filtered:
                    sections.append({"source": "web-chat (файл)", "count": len(filtered)})
                    lines.append("")
                    lines.append(f"════ web-chat · {path.name} ════")
                    lines.extend(filtered)
            except OSError as exc:
                lines.append(f"[не удалось прочитать {path}: {exc}]")

    return {
        "lines": lines,
        "sections": sections,
        "line_count": len(lines),
    }
