"""
Хеширование паролей (bcrypt).
"""

from __future__ import annotations

import bcrypt

_BCRYPT_ROUNDS = 12

# Заглушка для legacy X-Web-Chat-User (без реального пароля).
LEGACY_HEADER_PASSWORD_HASH = bcrypt.hashpw(
    b"__web_chat_header_user_no_password__",
    bcrypt.gensalt(rounds=_BCRYPT_ROUNDS),
).decode("ascii")


def hash_password(plain: str) -> str:
    """Хеш пароля для хранения в БД."""
    if not plain:
        raise ValueError("Пароль не может быть пустым")
    salt = bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
    return bcrypt.hashpw(plain.encode("utf-8"), salt).decode("ascii")


def verify_password(plain: str, password_hash: str) -> bool:
    """Сверить пароль с хешем (constant-time внутри bcrypt)."""
    if not plain or not password_hash:
        return False
    try:
        return bcrypt.checkpw(
            plain.encode("utf-8"),
            password_hash.encode("ascii"),
        )
    except (ValueError, TypeError):
        return False
