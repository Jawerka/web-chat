"""P5.2: XSS-санитизация markdown (зеркало static/js/markdown.js)."""

from __future__ import annotations

import re
from urllib.parse import urlparse


def is_allowed_uri(uri: str, *, for_image: bool = False) -> bool:
    """Порт isAllowedUri из static/js/markdown.js."""
    if not uri or not isinstance(uri, str):
        return False
    t = uri.strip()
    if not t or re.match(r"^\s*(javascript|vbscript|data):", t, re.I):
        return False
    if t.startswith("//"):
        return False
    if t.startswith("/media/") or t.startswith("/static/"):
        return True
    if t.startswith("/") and not t.startswith("//"):
        return t.startswith("/media/") if for_image else True
    try:
        parsed = urlparse(t)
        if parsed.scheme in ("http", "https"):
            if for_image:
                return parsed.path.startswith("/media/")
            return True
    except ValueError:
        return False
    return False


def sanitize_html_legacy(html: str) -> str:
    """Порт sanitizeHtmlLegacy из static/js/markdown.js."""
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
    out = sanitize_html_legacy(raw)
    assert "<script" not in out.lower()
    assert "alert" not in out


def test_blocks_javascript_urls() -> None:
    raw = '<a href="javascript:alert(document.cookie)">x</a>'
    out = sanitize_html_legacy(raw)
    assert "javascript:" not in out.lower()
    assert "blocked:" in out


def test_strips_onclick() -> None:
    raw = '<img src="/x.png" onerror="alert(1)">'
    out = sanitize_html_legacy(raw)
    assert "onerror" not in out.lower()


def test_strips_iframe() -> None:
    raw = '<iframe src="https://evil.example"></iframe>'
    out = sanitize_html_legacy(raw)
    assert "<iframe" not in out.lower()


def test_is_allowed_uri_media_path() -> None:
    assert is_allowed_uri("/media/asset/00000000-0000-4000-8000-000000000001")
    assert is_allowed_uri("/media/generated/foo.png", for_image=True)


def test_is_allowed_uri_blocks_javascript() -> None:
    assert not is_allowed_uri("javascript:alert(1)")
    assert not is_allowed_uri("javascript:alert(1)", for_image=True)


def test_is_allowed_uri_image_requires_media() -> None:
    assert is_allowed_uri("/health", for_image=False)
    assert not is_allowed_uri("/health", for_image=True)
    assert not is_allowed_uri("https://evil.example/x.png", for_image=True)


def test_is_allowed_uri_https_link_ok_for_href() -> None:
    assert is_allowed_uri("https://example.com/doc", for_image=False)
