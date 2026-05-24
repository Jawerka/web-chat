"""Проверка production-конфига AUTH (SEC-1)."""

from __future__ import annotations

import pytest

from app.config import Settings


def test_production_rejects_weak_bootstrap_password() -> None:
    with pytest.raises(ValueError, match="AUTH_BOOTSTRAP_ADMIN_PASSWORD"):
        Settings(
            web_chat_env="production",
            auth_enabled=True,
            auth_secret="x" * 32,
            auth_bootstrap_admin_password="admin",
        )


def test_production_allows_strong_bootstrap_password() -> None:
    s = Settings(
        web_chat_env="production",
        auth_enabled=True,
        auth_secret="x" * 32,
        auth_bootstrap_admin_password="Str0ng-Pass-Word!",
    )
    assert s.auth_enabled is True
