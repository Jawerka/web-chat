"""
Подписанные сессионные cookie (itsdangerous).
"""

from __future__ import annotations

import uuid
from typing import Any

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

SESSION_COOKIE_NAME = "webchat_session"
SESSION_PAYLOAD_VERSION = 1


def _serializer(secret: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret, salt="webchat-auth-session-v1")


def create_session_token(*, user_id: uuid.UUID, secret: str) -> str:
    """Создать подписанный токен сессии."""
    payload = {"v": SESSION_PAYLOAD_VERSION, "uid": str(user_id)}
    return _serializer(secret).dumps(payload)


def load_session_token(
    token: str,
    *,
    secret: str,
    max_age_sec: int,
) -> uuid.UUID | None:
    """Разобрать токен; None при невалидной или просроченной сессии."""
    if not token or not secret:
        return None
    try:
        data: dict[str, Any] = _serializer(secret).loads(
            token,
            max_age=max_age_sec,
        )
    except (BadSignature, SignatureExpired):
        return None
    if data.get("v") != SESSION_PAYLOAD_VERSION:
        return None
    raw_uid = data.get("uid")
    if not raw_uid:
        return None
    try:
        return uuid.UUID(str(raw_uid))
    except ValueError:
        return None
