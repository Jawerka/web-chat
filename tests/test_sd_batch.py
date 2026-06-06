"""batch_size / n_iter для txt2img."""

from __future__ import annotations

import pytest

from app.config import settings
from app.integrations.sd_batch import SD_BATCH_SIZE, clamp_txt2img_n_iter


def test_sd_batch_size_is_one() -> None:
    assert SD_BATCH_SIZE == 1


def test_clamp_txt2img_n_iter_respects_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "sd_txt2img_max_n_iter", 20)
    assert clamp_txt2img_n_iter(5) == 5
    assert clamp_txt2img_n_iter(100) == 20
    assert clamp_txt2img_n_iter(0) == 1
    assert clamp_txt2img_n_iter("3") == 3
