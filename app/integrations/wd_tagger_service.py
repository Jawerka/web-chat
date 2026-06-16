"""
Постоянно загруженный WD14 tagger worker (subprocess + JSON-lines IPC).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import threading
from pathlib import Path

from app.config import settings
from app.services.job_queue import heavy_job_queue

logger = logging.getLogger(__name__)

_WORKER_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "wd_tagger_worker.py"


class WdTaggerService:
    """Singleton: один subprocess с моделью в RAM."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._started = False

    @property
    def is_available(self) -> bool:
        return (
            settings.wd_tagger_enabled
            and self._proc is not None
            and self._proc.poll() is None
        )

    async def start(self) -> None:
        if not settings.wd_tagger_enabled:
            logger.info("WD tagger disabled (WD_TAGGER_ENABLED=false)")
            return
        await heavy_job_queue.run_sync(self._start_sync)

    async def stop(self) -> None:
        if not self._started:
            return
        await heavy_job_queue.run_sync(self._stop_sync)

    async def ping(self) -> bool:
        if not settings.wd_tagger_enabled:
            return False
        try:
            result = await heavy_job_queue.run_sync(self._ping_sync)
            return bool(result.get("ok"))
        except Exception:
            logger.warning("WD tagger ping failed", exc_info=True)
            return False

    async def tag_bytes(self, data: bytes, mime: str) -> str:
        """Теги для изображения; пустая строка при ошибке."""
        if not settings.wd_tagger_enabled:
            return ""
        suffix = _suffix_for_mime(mime)
        try:
            return await heavy_job_queue.run_sync(
                self._tag_bytes_sync,
                data,
                suffix,
            )
        except Exception:
            logger.warning("WD tagger tag_bytes failed", exc_info=True)
            return ""

    def _start_sync(self) -> None:
        if self._started and self._proc is not None and self._proc.poll() is None:
            return
        self._stop_sync()
        python = settings.wd_tagger_python
        if not Path(python).is_file():
            logger.warning("WD tagger python not found: %s", python)
            return
        if not Path(settings.wd_tagger_run_py).is_file():
            logger.warning("WD tagger run.py not found: %s", settings.wd_tagger_run_py)
            return
        if not _WORKER_SCRIPT.is_file():
            logger.warning("WD tagger worker script not found: %s", _WORKER_SCRIPT)
            return

        env = os.environ.copy()
        hf_home = settings.wd_tagger_hf_home
        env["HF_HOME"] = hf_home
        env["HF_HUB_CACHE"] = str(Path(hf_home) / "hub")
        env["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

        cmd = [
            python,
            str(_WORKER_SCRIPT),
            "--run-py",
            settings.wd_tagger_run_py,
            "--model",
            settings.wd_tagger_model,
            "--threshold",
            str(settings.wd_tagger_threshold),
        ]
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )
        self._started = True
        logger.info(
            "WD tagger worker started (pid=%s, model=%s)",
            self._proc.pid,
            settings.wd_tagger_model,
        )
        if settings.wd_tagger_warmup_on_start:
            try:
                self._request_sync({"cmd": "ping"}, restart=False)
            except Exception:
                logger.warning("WD tagger warmup ping failed", exc_info=True)

    def _stop_sync(self) -> None:
        proc = self._proc
        self._proc = None
        self._started = False
        if proc is None:
            return
        try:
            if proc.poll() is None and proc.stdin is not None:
                proc.stdin.write(json.dumps({"cmd": "shutdown"}) + "\n")
                proc.stdin.flush()
                proc.wait(timeout=5)
        except Exception:
            proc.kill()
        logger.info("WD tagger worker stopped")

    def _ping_sync(self) -> dict:
        return self._request_sync({"cmd": "ping"})

    def _tag_bytes_sync(self, data: bytes, suffix: str) -> str:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        try:
            result = self._request_sync({"cmd": "tag", "path": tmp_path})
            if not result.get("ok"):
                logger.warning("WD tagger error: %s", result.get("error"))
                return ""
            return str(result.get("tags") or "").strip()
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def _request_sync(self, payload: dict, *, restart: bool = True) -> dict:
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                if restart:
                    self._start_sync()
                if self._proc is None or self._proc.poll() is not None:
                    raise RuntimeError("WD tagger worker unavailable")
            assert self._proc.stdin is not None
            assert self._proc.stdout is not None
            self._proc.stdin.write(json.dumps(payload) + "\n")
            self._proc.stdin.flush()
            line = self._proc.stdout.readline()
            if not line:
                if restart:
                    self._stop_sync()
                    self._start_sync()
                    return self._request_sync(payload, restart=False)
                raise RuntimeError("WD tagger worker closed stdout")
            try:
                return json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"invalid worker response: {line!r}") from exc


def _suffix_for_mime(mime: str) -> str:
    m = (mime or "").lower().split(";")[0].strip()
    if m == "image/jpeg":
        return ".jpg"
    if m == "image/webp":
        return ".webp"
    if m == "image/gif":
        return ".gif"
    return ".png"


wd_tagger_service = WdTaggerService()
