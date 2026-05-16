"""Сериализация datetime в UTC ISO для API."""

from __future__ import annotations

from datetime import UTC, datetime


def ensure_utc_aware(dt: datetime) -> datetime:
    """Считать naive datetime из SQLite записью UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def datetime_to_utc_iso(dt: datetime) -> str:
    """ISO 8601 с суффиксом Z для JSON."""
    return ensure_utc_aware(dt).isoformat().replace("+00:00", "Z")
