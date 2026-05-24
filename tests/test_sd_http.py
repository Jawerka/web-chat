"""Тесты SD HTTP retry и circuit breaker (BE-2)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from app.integrations.sd_http import SdUnavailableError, reset_sd_circuit_for_tests, sd_post_json


@pytest.fixture(autouse=True)
def _reset_circuit() -> None:
    reset_sd_circuit_for_tests()
    yield
    reset_sd_circuit_for_tests()


def test_sd_post_json_success() -> None:
    session = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    session.post.return_value = resp

    out = sd_post_json(session, "http://sd/test", {"a": 1}, timeout=10, operation="t")
    assert out is resp
    session.post.assert_called_once()


def test_sd_circuit_opens_after_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.integrations.sd_http.settings.sd_http_retries", 0)
    monkeypatch.setattr("app.integrations.sd_http.settings.sd_circuit_breaker_threshold", 2)

    session = MagicMock()
    session.post.side_effect = requests.ConnectionError("refused")

    with pytest.raises(SdUnavailableError):
        sd_post_json(session, "http://sd/test", {}, timeout=1, operation="t")
    with pytest.raises(SdUnavailableError):
        sd_post_json(session, "http://sd/test", {}, timeout=1, operation="t")

    with pytest.raises(SdUnavailableError, match="временно недоступен"):
        sd_post_json(session, "http://sd/test", {}, timeout=1, operation="t")
