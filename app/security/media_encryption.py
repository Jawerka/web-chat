"""
Шифрование BLOB MediaAsset per-user (AES-256-GCM).
"""

from __future__ import annotations

import secrets
import uuid

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_NONCE_LEN = 12
_INFO = b"webchat-media-v1"
_VERSION_PLAIN = 0
_VERSION_ENCRYPTED = 1


def generate_media_token() -> bytes:
    """32 байта для users.media_token."""
    return secrets.token_bytes(32)


def derive_media_key(media_token: bytes, *, auth_secret: str, asset_id: uuid.UUID | None = None) -> bytes:
    """AES-256 ключ из токена пользователя и секрета приложения."""
    if len(media_token) < 16:
        raise ValueError("media_token слишком короткий")
    salt = auth_secret.encode("utf-8")[:32]
    if len(salt) < 16:
        salt = (salt + b"\0" * 32)[:32]
    hkdf = HKDF(
        algorithm=SHA256(),
        length=32,
        salt=salt,
        info=_INFO,
    )
    key = hkdf.derive(media_token)
    if asset_id is not None:
        # Дополнительная привязка не меняет ключ в v1 (AAD в encrypt)
        pass
    return key


def encrypt_blob(
    plaintext: bytes,
    *,
    media_token: bytes,
    auth_secret: str,
    asset_id: uuid.UUID | None = None,
) -> bytes:
    """Ciphertext: nonce (12) + AES-GCM tag+data."""
    key = derive_media_key(media_token, auth_secret=auth_secret, asset_id=asset_id)
    nonce = secrets.token_bytes(_NONCE_LEN)
    aad = str(asset_id).encode("utf-8") if asset_id else None
    ct = AESGCM(key).encrypt(nonce, plaintext, aad)
    return nonce + ct


def decrypt_blob(
    ciphertext: bytes,
    *,
    media_token: bytes,
    auth_secret: str,
    asset_id: uuid.UUID | None = None,
) -> bytes:
    """Расшифровать blob, записанный encrypt_blob."""
    if len(ciphertext) < _NONCE_LEN + 16:
        raise ValueError("ciphertext слишком короткий")
    nonce = ciphertext[:_NONCE_LEN]
    payload = ciphertext[_NONCE_LEN:]
    key = derive_media_key(media_token, auth_secret=auth_secret, asset_id=asset_id)
    aad = str(asset_id).encode("utf-8") if asset_id else None
    return AESGCM(key).decrypt(nonce, payload, aad)


def encryption_version_encrypted() -> int:
    return _VERSION_ENCRYPTED


def encryption_version_plain() -> int:
    return _VERSION_PLAIN
