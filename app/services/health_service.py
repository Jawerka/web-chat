"""
Сбор метрик и проверок для дашборда /health.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import socket
import time
from datetime import UTC, datetime
from pathlib import Path
from collections import deque
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.api.ws_manager import manager
from app.config import settings
from app.integrations.media_utils import GENERATED_ROOT
from app.db import session as db_session
from app.logging_buffer import get_log_lines
from app.services.job_queue import heavy_job_queue

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
    ws_connections: int = 0
    job_queue_pending: int = 0
    disk_free_mb: float = 0
    generated_count: int = 0


HISTORY_INTERVAL_SEC = 30
HISTORY_WINDOW_SEC = 3600
HISTORY_MAX_POINTS = HISTORY_WINDOW_SEC // HISTORY_INTERVAL_SEC

_HISTORY: deque[HealthHistoryPoint] = deque(maxlen=HISTORY_MAX_POINTS)
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
                job_count = int(pdata.get("state", {}).get("job_count") or 0)
                if job_count > 0 and pct >= 5:
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
    from app.db.url import active_database_url, is_postgres_url, is_sqlite_url

    url = active_database_url()
    if is_postgres_url(url):
        return _postgres_db_extra()
    if is_sqlite_url(url):
        return _sqlite_db_extra(url)
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


def _sqlite_db_extra(url: str) -> dict[str, Any]:
    """Размер WAL и счётчик retry busy (P1.1)."""
    from pathlib import Path

    from app.db.sqlite import sqlite_busy_retries_total

    extra: dict[str, Any] = {
        "backend": "sqlite",
        "sqlite_busy_retries": sqlite_busy_retries_total(),
    }
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
    from app.db.url import active_database_url, is_postgres_url

    url = active_database_url()
    t0 = time.perf_counter()
    try:
        async with db_session.async_session_factory() as session:
            await session.execute(text("SELECT 1"))
            db_size: str | None = None
            if is_postgres_url(url):
                row = await session.execute(
                    text("SELECT pg_size_pretty(pg_database_size(current_database()))"),
                )
                db_size = row.scalar()
        latency = (time.perf_counter() - t0) * 1000
        extra = _db_extra()
        if is_postgres_url(url):
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


_GENERATED_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})


def _generated_disk_count() -> int:
    """Число файлов изображений в data/generated/ (без обхода thumbs/)."""
    if not GENERATED_ROOT.is_dir():
        return 0
    return sum(
        1
        for path in GENERATED_ROOT.iterdir()
        if path.is_file() and path.suffix.lower() in _GENERATED_IMAGE_SUFFIXES
    )


def _data_disk_extra() -> dict[str, Any]:
    """Свободное место на томе с каталогом data/ (P1.6, P4.5)."""
    data_dir = Path("data")
    root = data_dir if data_dir.is_dir() else Path(".")
    usage = shutil.disk_usage(root)
    free_mb = round(usage.free / (1024**2), 1)
    free_gb = round(usage.free / (1024**3), 2)
    used_pct = round(100 * usage.used / usage.total, 1) if usage.total else 0
    return {
        "data_free_mb": free_mb,
        "data_free_gb": free_gb,
        "data_disk_used_percent": used_pct,
    }


def disk_free_mb() -> float:
    """Свободное место на томе data/ в мегабайтах (для ops / JSON health)."""
    return float(_data_disk_extra()["data_free_mb"])


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


def _overall_from_probes(
    llm_probe: ServiceProbe,
    sd_probe: ServiceProbe,
    db_probe: ServiceProbe,
) -> Literal["ok", "degraded"]:
    critical = [llm_probe, sd_probe, db_probe]
    if any(s.status == "unavailable" for s in critical):
        return "degraded"
    if any(s.status in ("loading", "degraded") for s in critical):
        return "degraded"
    return "ok"


async def record_health_history_tick() -> None:
    """Один замер для графика доступности (каждые HISTORY_INTERVAL_SEC)."""
    llm_probe = await _probe_llm()
    sd_probe = await _probe_sd()
    db_probe = await _probe_database()
    overall = _overall_from_probes(llm_probe, sd_probe, db_probe)
    _HISTORY.append(
        HealthHistoryPoint(
            ts=time.time(),
            overall=_score("ok" if overall == "ok" else "degraded"),
            llm=_score(llm_probe.status),
            sd=_score(sd_probe.status),
            database=_score(db_probe.status),
        ),
    )


async def health_history_background(stop: asyncio.Event) -> None:
    """Фоновый сбор метрик для графика (60 мин × шаг 30 с)."""
    await record_health_history_tick()
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=HISTORY_INTERVAL_SEC)
            break
        except asyncio.TimeoutError:
            pass
        try:
            await record_health_history_tick()
        except Exception:
            logger.exception("health history tick failed")


async def build_health_report() -> HealthReport:
    """Собрать полный отчёт (история — из фонового сборщика)."""
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

    overall = _overall_from_probes(llm_probe, sd_probe, db_probe)

    disk = _data_disk_extra()
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
        ws_connections=manager.websocket_count(),
        job_queue_pending=heavy_job_queue.pending_count,
        disk_free_mb=float(disk["data_free_mb"]),
        generated_count=_generated_disk_count(),
    )


_SERVER_TS_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:[,.](\d+))?",
)
_CLIENT_TS_RE = re.compile(r"^\[(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?\]")


def _parse_log_line_ts(line: str, *, now: datetime | None = None) -> float | None:
    """Извлечь unix timestamp из строки серверного или клиентского журнала."""
    ref = now or datetime.now(UTC)
    m = _SERVER_TS_RE.match(line)
    if m:
        frac = m.group(2) or "0"
        text = f"{m.group(1)}.{frac[:6]}"
        try:
            dt = datetime.strptime(text, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=UTC)
            return dt.timestamp()
        except ValueError:
            return None
    m = _CLIENT_TS_RE.match(line)
    if m:
        h, mi, s = int(m.group(1)), int(m.group(2)), int(m.group(3))
        ms = int((m.group(4) or "0")[:3])
        dt = ref.replace(hour=h, minute=mi, second=s, microsecond=ms * 1000)
        return dt.timestamp()
    return None


def _server_dedup_key(line: str) -> str:
    """Ключ дедупликации: секунда + тело строки (буфер и файл пишут одно и то же)."""
    m = _SERVER_TS_RE.match(line)
    if m:
        return f"{m.group(1)}|{line[m.end():].strip()}"
    return line.strip()


def _merge_backend_lines(mem: list[str], file_lines: list[str]) -> list[str]:
    """Объединить буфер и хвост файла без дублей, по времени."""
    seen: set[str] = set()
    merged: list[str] = []
    for ln in file_lines + mem:
        key = _server_dedup_key(ln)
        if key in seen:
            continue
        seen.add(key)
        merged.append(ln)
    merged.sort(key=lambda ln: _parse_log_line_ts(ln) or 0.0)
    return merged


_CLIENT_NOISE_FRAGMENTS = (
    "[DEBUG] [health] Журнал загружен",
    "[DEBUG] [health] fetchLogs",
)


def _filter_client_lines(lines: list[str]) -> list[str]:
    return [
        ln
        for ln in lines
        if not any(frag in ln for frag in _CLIENT_NOISE_FRAGMENTS)
    ]


def _filter_lines_by_age(
    lines: list[str],
    *,
    since_hours: float | None,
) -> list[str]:
    if since_hours is None or since_hours <= 0:
        return lines
    cutoff = time.time() - since_hours * 3600.0
    now = datetime.now(UTC)
    kept: list[str] = []
    for ln in lines:
        ts = _parse_log_line_ts(ln, now=now)
        if ts is None or ts >= cutoff:
            kept.append(ln)
    return kept


def collect_aggregate_logs(
    *,
    buffer_limit: int = 4000,
    file_tail: int = 4000,
    client_limit: int = 2000,
    since_hours: float | None = None,
) -> dict[str, Any]:
    """Объединённый журнал: сервер (память + файл) + клиент (браузер)."""
    from app.logging_buffer import get_client_log_lines

    sections: list[dict[str, Any]] = []
    lines: list[str] = []
    _skip_fragments = ("http://llm.test/",)

    mem = [
        ln for ln in get_log_lines(limit=buffer_limit)
        if not any(s in ln for s in _skip_fragments)
    ]
    file_lines: list[str] = []
    log_path = settings.log_file.strip()
    if log_path:
        path = Path(log_path)
        if path.is_file():
            try:
                raw = path.read_text(encoding="utf-8", errors="replace").splitlines()
                tail = raw[-file_tail:] if len(raw) > file_tail else raw
                file_lines = [ln for ln in tail if not any(s in ln for s in _skip_fragments)]
            except OSError as exc:
                lines.append(f"[не удалось прочитать {path}: {exc}]")

    backend = _merge_backend_lines(mem, file_lines)
    backend = _filter_lines_by_age(backend, since_hours=since_hours)
    if backend:
        sections.append({"source": "backend", "count": len(backend)})
        lines.append("════ backend ════")
        lines.extend(backend)

    client = _filter_client_lines(get_client_log_lines(limit=client_limit))
    if client:
        filtered = _filter_lines_by_age(client, since_hours=since_hours)
        if filtered:
            sections.append({"source": "frontend (браузер)", "count": len(filtered)})
            lines.append("")
            lines.append("════ frontend · браузер ════")
            lines.extend(filtered)

    return {
        "lines": lines,
        "sections": sections,
        "line_count": len(lines),
    }
