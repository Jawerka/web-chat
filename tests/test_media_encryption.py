"""Шифрование media BLOB."""

from __future__ import annotations

import uuid

import pytest

from app.security.media_encryption import decrypt_blob, encrypt_blob, generate_media_token


def test_encrypt_roundtrip() -> None:
    token = generate_media_token()
    secret = "a" * 32
    aid = uuid.uuid4()
    plain = b"test image bytes"
    ct = encrypt_blob(plain, media_token=token, auth_secret=secret, asset_id=aid)
    assert ct != plain
    out = decrypt_blob(ct, media_token=token, auth_secret=secret, asset_id=aid)
    assert out == plain
