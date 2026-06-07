#!/usr/bin/env python3
"""Снимок состояния web-chat для дебага. См. docs/debug-snapshots/."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.db.session import async_session_factory, init_db  # noqa: E402


def sh(cmd: str) -> str:
    try:
        return subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.STDOUT).strip()
    except subprocess.CalledProcessError as exc:
        return f"ERROR: {exc.output}"


def count_files(base: Path, exts: set[str]) -> int:
    if not base.is_dir():
        return 0
    return sum(1 for f in base.rglob("*") if f.is_file() and f.suffix.lower() in exts)


async def db_stats() -> dict:
    await init_db()
    stats: dict = {}
    queries = {
        "users": "SELECT login, role, created_at::text FROM users ORDER BY created_at",
        "assets_by_kind": """
            SELECT COALESCE(gallery_kind,'NULL'), COUNT(*),
                   ROUND(SUM(length(data))/1024.0/1024, 2)
            FROM media_assets WHERE mime_type LIKE 'image/%' GROUP BY 1 ORDER BY 2 DESC
        """,
        "assets_by_kind_owner": """
            SELECT u.login, COALESCE(m.gallery_kind,'NULL'), COUNT(*)
            FROM media_assets m LEFT JOIN users u ON u.id=m.owner_user_id
            WHERE m.mime_type LIKE 'image/%' GROUP BY 1,2 ORDER BY 1,3 DESC
        """,
        "uploads_timeline": """
            SELECT created_at::date, COUNT(*) FROM media_assets
            WHERE gallery_kind='upload' GROUP BY 1 ORDER BY 1
        """,
        "total_assets": "SELECT COUNT(*) FROM media_assets",
        "total_conversations": "SELECT COUNT(*) FROM conversations",
        "alembic": "SELECT version_num FROM alembic_version",
    }
    async with async_session_factory() as session:
        for name, sql in queries.items():
            result = await session.execute(text(sql))
            stats[name] = [list(row) for row in result.fetchall()]
    return stats


def api_counts() -> dict:
    cookie = "/tmp/wc-snap-cookies.txt"
    sh(
        "curl -s -c {c} -X POST http://127.0.0.1:8090/api/auth/login "
        "-H 'Content-Type: application/json' "
        "-d '{{\"login\":\"admin\",\"password\":\"admin\"}}'".format(c=cookie),
    )
    out: dict = {}
    for path, key in [
        ("/api/gallery?limit=1000", "generation_gallery"),
        ("/api/gallery/uploads?limit=5000", "uploads_gallery"),
    ]:
        raw = sh(f"curl -s -b {cookie} 'http://127.0.0.1:8090{path}'")
        try:
            data = json.loads(raw)
            imgs = data.get("images", [])
            out[key] = {
                "count": data.get("count", len(imgs)),
                "sources": sorted({i.get("source") for i in imgs}),
            }
        except json.JSONDecodeError:
            out[key] = {"error": raw[:300]}
    return out


async def main() -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    stamp = now.replace(":", "").replace("-", "")
    img_exts = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

    snapshot = {
        "captured_at_utc": now,
        "hostname": sh("hostname"),
        "git": {
            "head": sh(f"git -C {ROOT} rev-parse --short HEAD"),
            "branch": sh(f"git -C {ROOT} branch --show-current"),
            "status_short": sh(f"git -C {ROOT} status --short"),
            "last_commit": sh(f"git -C {ROOT} log -1 --format='%h %ci %s'"),
        },
        "server": {
            "uvicorn": sh("ps aux | grep 'uvicorn app.main' | grep -v grep | head -1"),
            "health": sh("curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8090/api/health"),
        },
        "config_public": {
            "web_port": settings.web_port,
            "auth_enabled": settings.auth_enabled,
            "database_backend": "postgresql" if "postgresql" in settings.database_url else "sqlite",
            "upload_retention_days": settings.upload_retention_days,
            "generated_retention_days": settings.generated_retention_days,
            "api_access_key_set": bool((settings.api_access_key or "").strip()),
        },
        "disk": {
            "data_generated_images": count_files(ROOT / "data/generated", img_exts),
            "data_uploads_files": count_files(
                ROOT / "data/uploads",
                img_exts | {".pdf"},
            ),
            "backups_database_archives": len(
                list((ROOT / "data/backups/database").glob("*.tar.gz")),
            ),
        },
        "database": await db_stats(),
        "api_as_admin": api_counts(),
    }

    out_dir = ROOT / "docs" / "debug-snapshots"
    out_dir.mkdir(parents=True, exist_ok=True)
    archive = out_dir / f"state-{stamp}.json"
    latest_json = out_dir / "latest.json"
    latest_md = out_dir / "latest.md"

    payload = json.dumps(snapshot, ensure_ascii=False, indent=2, default=str)
    archive.write_text(payload, encoding="utf-8")
    latest_json.write_text(payload, encoding="utf-8")

    gen = snapshot["api_as_admin"].get("generation_gallery", {}).get("count", "?")
    upl = snapshot["api_as_admin"].get("uploads_gallery", {}).get("count", "?")
    kinds = "\n".join(str(r) for r in snapshot["database"]["assets_by_kind"])
    owners = "\n".join(str(r) for r in snapshot["database"]["assets_by_kind_owner"])

    latest_md.write_text(
        f"""# web-chat debug snapshot

**Captured:** {now} UTC
**Git:** `{snapshot['git']['head']}` — {snapshot['git']['last_commit']}

## Quick summary

| Metric | Value |
|--------|-------|
| Generation gallery (API) | {gen} |
| Uploads gallery (API) | {upl} |
| Disk data/generated images | {snapshot['disk']['data_generated_images']} |
| Disk data/uploads files | {snapshot['disk']['data_uploads_files']} |
| DB backup archives | {snapshot['disk']['backups_database_archives']} |
| Auth enabled | {snapshot['config_public']['auth_enabled']} |
| Alembic | {snapshot['database']['alembic']} |

## Assets by gallery_kind (DB)

```
{kinds}
```

## Assets by user (DB)

```
{owners}
```

## Uncommitted changes

```
{snapshot['git']['status_short'] or '(clean)'}
```

## Files

- Archive: `{archive.name}`
- Latest JSON: `latest.json`
""",
        encoding="utf-8",
    )

    print(archive)
    print(latest_json)
    print(latest_md)


if __name__ == "__main__":
    asyncio.run(main())
