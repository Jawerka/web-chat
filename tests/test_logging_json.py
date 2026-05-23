"""JSON-формат логов (P1.6)."""

from __future__ import annotations

import json
import logging

from app.logging_setup import JsonLogFormatter


def test_json_log_formatter() -> None:
    fmt = JsonLogFormatter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    record.conv_id = "c1"
    record.turn = "t1"
    record.ws_session = "w1"
    line = fmt.format(record)
    data = json.loads(line)
    assert data["message"] == "hello"
    assert data["level"] == "INFO"
    assert data["conv_id"] == "c1"
