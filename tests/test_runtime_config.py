"""Тесты парсинга URL интеграций из WebSocket."""

from __future__ import annotations

from app.integrations.runtime_config import (
    parse_integration_overrides,
    parse_optional_url,
    resolve_llm_base_url,
)


def test_parse_optional_url_valid() -> None:
    assert parse_optional_url(" http://127.0.0.1:8989/v1/ ") == "http://127.0.0.1:8989/v1"


def test_parse_optional_url_rejects_invalid() -> None:
    assert parse_optional_url("ftp://x") is None
    assert parse_optional_url("") is None


def test_parse_integration_overrides() -> None:
    data = {
        "model": "my-model",
        "llm_base_url": "http://a/v1",
        "sd_webui_url": "http://b:7860",
    }
    o = parse_integration_overrides(data)
    assert o.llm_model == "my-model"
    assert o.llm_base_url == "http://a/v1"
    assert o.sd_webui_url == "http://b:7860"


def test_resolve_llm_base_url_override() -> None:
    assert resolve_llm_base_url("http://custom/v1").endswith("/v1")
