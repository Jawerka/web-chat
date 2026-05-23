"""Тесты trust boundary URL (P0.5)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.integrations.media_utils import resolve_trusted_generated_source
from app.public_url import is_trusted_media_url, resolve_public_base_url


@pytest.fixture
def dual_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://192.168.88.44:8090")
    monkeypatch.setenv("PUBLIC_BASE_URL_VPN", "http://10.99.99.9:8090")
    s = Settings()
    monkeypatch.setattr("app.public_url.settings", s)
    monkeypatch.setattr("app.config.settings", s)


def test_settings_rejects_link_local_metadata_ip() -> None:
    with pytest.raises(ValidationError, match="недопустимый host"):
        Settings(public_base_url="http://169.254.169.254/")


def test_settings_allows_lan_host() -> None:
    s = Settings(public_base_url="http://192.168.88.44:8090")
    assert s.public_base_url == "http://192.168.88.44:8090"


def test_settings_allows_localhost_dev() -> None:
    s = Settings(public_base_url="http://127.0.0.1:8090")
    assert "127.0.0.1" in s.public_base_url


def test_is_trusted_media_url_rejects_external(dual_urls: None) -> None:
    assert not is_trusted_media_url("https://evil.example.com/media/asset/x")
    assert not is_trusted_media_url("http://169.254.169.254/media/asset/x")


def test_is_trusted_media_url_accepts_relative_and_lan(dual_urls: None) -> None:
    assert is_trusted_media_url("/media/asset/550e8400-e29b-41d4-a716-446655440000")
    assert is_trusted_media_url("http://192.168.88.44:8090/media/asset/x")


def test_for_llm_ignores_vpn_host(dual_urls: None) -> None:
    from app.public_url import bind_request_public_base_url, reset_request_public_base_url

    token = bind_request_public_base_url(host="10.99.99.9:8090", client_host="10.99.99.2")
    try:
        assert resolve_public_base_url(for_llm=True) == "http://192.168.88.44:8090"
    finally:
        reset_request_public_base_url(token)


def test_resolve_trusted_generated_rejects_arbitrary_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.integrations.media_utils.settings.public_base_url",
        "http://192.168.88.44:8090",
    )
    with pytest.raises(ValueError, match="Недопустимый"):
        resolve_trusted_generated_source("http://169.254.169.254/secret.png")
