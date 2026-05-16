"""Тесты выбора PUBLIC_BASE_URL (LAN / VPN)."""

from __future__ import annotations

import pytest

from app.public_url import (
    absolute_media_path,
    all_public_base_urls,
    bind_request_public_base_url,
    public_base_url_lan,
    public_base_url_vpn,
    reset_request_public_base_url,
    resolve_public_base_url,
    strip_public_base,
)


@pytest.fixture
def dual_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://192.168.88.44:8090")
    monkeypatch.setenv("PUBLIC_BASE_URL_VPN", "http://10.99.99.9:8090")
    from app.config import Settings

    s = Settings()
    monkeypatch.setattr("app.public_url.settings", s)
    monkeypatch.setattr("app.config.settings", s)


def test_all_public_base_urls(dual_urls: None) -> None:
    assert public_base_url_lan() == "http://192.168.88.44:8090"
    assert public_base_url_vpn() == "http://10.99.99.9:8090"
    assert len(all_public_base_urls()) == 2


def test_resolve_vpn_by_host(dual_urls: None) -> None:
    token = bind_request_public_base_url(host="10.99.99.9:8090", client_host="10.99.99.2")
    try:
        assert resolve_public_base_url() == "http://10.99.99.9:8090"
        assert absolute_media_path("/media/asset/x") == "http://10.99.99.9:8090/media/asset/x"
    finally:
        reset_request_public_base_url(token)


def test_resolve_lan_by_host(dual_urls: None) -> None:
    token = bind_request_public_base_url(host="192.168.88.44:8090", client_host="192.168.88.10")
    try:
        assert resolve_public_base_url() == "http://192.168.88.44:8090"
    finally:
        reset_request_public_base_url(token)


def test_for_llm_always_lan(dual_urls: None) -> None:
    token = bind_request_public_base_url(host="10.99.99.9:8090", client_host="10.99.99.2")
    try:
        assert resolve_public_base_url(for_llm=True) == "http://192.168.88.44:8090"
        assert absolute_media_path("/media/asset/x", for_llm=True) == (
            "http://192.168.88.44:8090/media/asset/x"
        )
    finally:
        reset_request_public_base_url(token)


def test_strip_public_base(dual_urls: None) -> None:
    assert strip_public_base("http://10.99.99.9:8090/media/generated/a.png") == (
        "/media/generated/a.png"
    )
    assert strip_public_base("http://192.168.88.44:8090/media/asset/1") == "/media/asset/1"
