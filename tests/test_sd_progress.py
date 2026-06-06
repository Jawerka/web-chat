"""Парсинг SD WebUI /sdapi/v1/progress."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.integrations.sd_progress import fetch_sd_progress


def test_fetch_sd_progress_active_job() -> None:
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "progress": 0.42,
        "state": {
            "sampling_step": 9,
            "sampling_steps": 22,
            "job_no": 0,
            "job_count": 1,
        },
        "textinfo": "",
        "eta_relative": 12,
    }
    mock_session = MagicMock()
    mock_session.get.return_value = mock_resp

    with patch("app.integrations.sd_progress.get_sd_session", return_value=mock_session):
        snap = fetch_sd_progress("http://sd.test:7860")

    assert snap is not None
    assert snap["active"] is True
    assert snap["percent"] == 42
    assert "9/22" in snap["detail"]


def test_fetch_sd_progress_includes_preview() -> None:
    tiny_png_b64 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "progress": 0.2,
        "state": {"sampling_step": 2, "sampling_steps": 10, "job_count": 1},
        "current_image": tiny_png_b64,
    }
    mock_session = MagicMock()
    mock_session.get.return_value = mock_resp

    with patch("app.integrations.sd_progress.get_sd_session", return_value=mock_session):
        snap = fetch_sd_progress("http://sd.test:7860")

    assert snap is not None
    assert snap["preview"] == f"data:image/png;base64,{tiny_png_b64}"


def test_fetch_sd_progress_idle() -> None:
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "progress": 0,
        "state": {"job_count": 0},
    }
    mock_session = MagicMock()
    mock_session.get.return_value = mock_resp

    with patch("app.integrations.sd_progress.get_sd_session", return_value=mock_session):
        snap = fetch_sd_progress()

    assert snap is not None
    assert snap["active"] is False
