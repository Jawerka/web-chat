"""Сериализация UTC datetime в API."""

from __future__ import annotations

from datetime import UTC, datetime

from app.datetime_utils import datetime_to_utc_iso


def test_naive_datetime_serialized_as_utc_z() -> None:
    dt = datetime(2026, 5, 16, 15, 27, 20, 291565)
    assert datetime_to_utc_iso(dt) == "2026-05-16T15:27:20.291565Z"


def test_aware_datetime_serialized_as_utc_z() -> None:
    dt = datetime(2026, 5, 16, 18, 27, 20, tzinfo=UTC)
    assert datetime_to_utc_iso(dt).endswith("Z")
