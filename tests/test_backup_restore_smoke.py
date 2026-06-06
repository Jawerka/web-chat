"""Smoke: scripts/backup-database.sh + restore-database.sh на temp SQLite (P6.7)."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKUP_SCRIPT = REPO_ROOT / "scripts" / "backup-database.sh"
RESTORE_SCRIPT = REPO_ROOT / "scripts" / "restore-database.sh"
VENV_PYTHON = REPO_ROOT / ".venv" / "bin" / "python"


def _sqlite_url(db_path: Path) -> str:
    return f"sqlite+aiosqlite:///{db_path.resolve()}"


def _write_marker(db_path: Path, marker: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS backup_smoke (marker TEXT NOT NULL)")
    conn.execute("DELETE FROM backup_smoke")
    conn.execute("INSERT INTO backup_smoke (marker) VALUES (?)", (marker,))
    conn.commit()
    conn.close()


def _read_marker(db_path: Path) -> str:
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT marker FROM backup_smoke").fetchone()
    conn.close()
    assert row is not None
    return str(row[0])


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
@pytest.mark.skipif(shutil.which("sqlite3") is None, reason="sqlite3 required")
@pytest.mark.skipif(not BACKUP_SCRIPT.is_file(), reason="backup script missing")
@pytest.mark.skipif(not VENV_PYTHON.is_file(), reason="project .venv required")
def test_sqlite_backup_and_restore_roundtrip() -> None:
    """Dry-run: бэкап temp SQLite → изменение → restore --yes → маркер из архива."""
    with tempfile.TemporaryDirectory(prefix="webchat-backup-smoke-") as tmp:
        root = Path(tmp)
        db_dir = root / "data" / "db"
        db_dir.mkdir(parents=True)
        db_path = db_dir / "web_chat.sqlite"
        backup_dir = root / "data" / "backups" / "database"
        backup_dir.mkdir(parents=True)

        _write_marker(db_path, "v1")

        db_url = _sqlite_url(db_path)
        (root / ".env").write_text(f"DATABASE_URL={db_url}\n", encoding="utf-8")
        if not (root / ".venv").exists():
            (root / ".venv").symlink_to(REPO_ROOT / ".venv", target_is_directory=True)
        if not (root / "scripts").exists():
            (root / "scripts").symlink_to(REPO_ROOT / "scripts", target_is_directory=True)

        env = os.environ.copy()
        env["WEB_CHAT_ROOT"] = str(root)
        env["DATABASE_URL"] = db_url
        env["WEB_CHAT_DB_BACKUP_DIR"] = str(backup_dir)
        env["WEB_CHAT_DB_BACKUP_KEEP"] = "5"
        env["PYTHONPATH"] = str(REPO_ROOT)

        proc = subprocess.run(
            ["bash", str(root / "scripts" / "backup-database.sh")],
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr or proc.stdout

        archives = sorted(backup_dir.glob("web-chat-db-*.tar.gz"))
        assert archives, "backup archive not created"
        archive = archives[-1]

        with tarfile.open(archive, "r:gz") as tar:
            names = tar.getnames()
            manifest_member = next(
                (m for m in tar.getmembers() if m.name.rstrip("/").endswith("manifest.json")),
                None,
            )
            assert manifest_member is not None
            manifest = json.loads(tar.extractfile(manifest_member).read().decode())
        assert manifest.get("backup_version") == 2
        assert manifest.get("database_backend") == "sqlite"
        assert any(n.endswith("web_chat.sqlite") for n in names)

        _write_marker(db_path, "v2")
        assert _read_marker(db_path) == "v2"

        proc = subprocess.run(
            [
                "bash",
                str(root / "scripts" / "restore-database.sh"),
                "--yes",
                "--no-safety-backup",
                "--file",
                str(archive),
            ],
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr or proc.stdout
        assert _read_marker(db_path) == "v1"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
def test_backup_database_list_exits_zero_on_empty_dir() -> None:
    """restore-database.sh --list на пустом каталоге — понятная ошибка (не segfault)."""
    with tempfile.TemporaryDirectory(prefix="webchat-backup-list-") as tmp:
        root = Path(tmp)
        backup_dir = root / "data" / "backups" / "database"
        backup_dir.mkdir(parents=True)
        (root / "scripts").symlink_to(REPO_ROOT / "scripts", target_is_directory=True)
        env = os.environ.copy()
        env["WEB_CHAT_ROOT"] = str(root)
        env["WEB_CHAT_DB_BACKUP_DIR"] = str(backup_dir)
        proc = subprocess.run(
            ["bash", str(root / "scripts" / "restore-database.sh"), "--list"],
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert proc.returncode == 0
