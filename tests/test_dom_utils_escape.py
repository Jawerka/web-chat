"""P5.5: зеркало escapeAttr из static/js/dom-utils.js."""

from __future__ import annotations


def escape_attr(value: str | None) -> str:
    if value is None:
        return ""
    return (
        str(value)
        .replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def test_escape_attr_quotes_and_ampersand() -> None:
    assert escape_attr('a&b"c') == "a&amp;b&quot;c"


def test_escape_attr_angle_brackets() -> None:
    assert escape_attr("<script>") == "&lt;script&gt;"
