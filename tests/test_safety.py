"""Тесты модуля tests.safety."""

from __future__ import annotations

import pytest

from tests.safety import (
    is_production_database_url,
    is_safe_test_database_url,
    live_cleanup_explicitly_enabled,
    production_database_url,
    resolve_safe_live_cleanup_base_url,
)


def test_production_url_not_safe_for_cleanup() -> None:
    prod = production_database_url()
    assert is_production_database_url(prod)
    assert not is_safe_test_database_url(prod)


def test_tmp_sqlite_is_safe() -> None:
    url = "sqlite+aiosqlite:////tmp/pytest-of-user/test.sqlite"
    assert is_safe_test_database_url(url)
    assert not is_production_database_url(url)


def test_live_cleanup_requires_explicit_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WEB_CHAT_TEST_BASE_URL", raising=False)
    monkeypatch.delenv("WEB_CHAT_TEST_CLEANUP_LIVE", raising=False)
    monkeypatch.delenv("WEB_CHAT_TEST_ALLOW_PUBLIC_CLEANUP", raising=False)
    assert not live_cleanup_explicitly_enabled()
    assert resolve_safe_live_cleanup_base_url() is None


def test_live_cleanup_explicit_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_CHAT_TEST_BASE_URL", "http://test-host:8099")
    assert live_cleanup_explicitly_enabled()
    assert resolve_safe_live_cleanup_base_url() == "http://test-host:8099"
