"""WdTaggerService IPC (mock subprocess)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.config import settings
from app.integrations.wd_tagger_service import WdTaggerService


@pytest.fixture
def tagger() -> WdTaggerService:
    return WdTaggerService()


@pytest.mark.asyncio
async def test_tag_bytes_returns_empty_when_disabled(tagger: WdTaggerService) -> None:
    result = await tagger.tag_bytes(b"\x89PNG", "image/png")
    assert result == ""


def test_request_sync_parses_tag_response(tagger: WdTaggerService, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "wd_tagger_enabled", True)

    proc = MagicMock()
    proc.poll.return_value = None
    proc.stdin = MagicMock()
    proc.stdout = MagicMock()
    proc.stdout.readline.return_value = json.dumps({"ok": True, "tags": "1girl, solo"}) + "\n"
    tagger._proc = proc
    tagger._started = True

    result = tagger._request_sync({"cmd": "tag", "path": "/tmp/x.png"})
    assert result["ok"] is True
    assert result["tags"] == "1girl, solo"
    proc.stdin.write.assert_called_once()
