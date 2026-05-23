"""P1.7: XSS-санитизация markdown (зеркало static/js/markdown.js)."""

from __future__ import annotations

import re


def sanitize_html(html: str) -> str:
    """Порт sanitizeHtml из static/js/markdown.js для тестов."""
    if not html:
        return html
    sanitized = html
    sanitized = re.sub(r"<script[\s\S]*?</script>", "", sanitized, flags=re.I)
    sanitized = re.sub(r"<script[\s\S]*", "", sanitized, flags=re.I)
    sanitized = re.sub(r"javascript\s*:", "blocked:", sanitized, flags=re.I)
    sanitized = re.sub(
        r'\s+on\w+\s*=\s*(?:"[^"]*"|\'[^\']*\'|[^\s>]+)',
        "",
        sanitized,
        flags=re.I,
    )
    sanitized = re.sub(
        r"<(?:iframe|object|embed)[\s\S]*?(?:</\w+>|/?>)",
        "",
        sanitized,
        flags=re.I,
    )
    sanitized = re.sub(r"<style[\s\S]*?</style>", "", sanitized, flags=re.I)
    return sanitized


def test_strips_script_tags() -> None:
    raw = '<p>Hi</p><script>alert(1)</script><p>bye</p>'
    out = sanitize_html(raw)
    assert "<script" not in out.lower()
    assert "alert" not in out


def test_blocks_javascript_urls() -> None:
    raw = '<a href="javascript:alert(document.cookie)">x</a>'
    out = sanitize_html(raw)
    assert "javascript:" not in out.lower()
    assert "blocked:" in out


def test_strips_onclick() -> None:
    raw = '<img src="/x.png" onerror="alert(1)">'
    out = sanitize_html(raw)
    assert "onerror" not in out.lower()


def test_strips_iframe() -> None:
    raw = '<iframe src="https://evil.example"></iframe>'
    out = sanitize_html(raw)
    assert "<iframe" not in out.lower()
