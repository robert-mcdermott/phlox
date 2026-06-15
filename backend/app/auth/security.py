"""Password hashing (bcrypt) + JWT session tokens (PyJWT, HS256)."""
from __future__ import annotations

import time
from typing import Any

import bcrypt
import jwt

from app.config import get_auth_config


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(user_id: str, role: str) -> str:
    cfg = get_auth_config()
    now = int(time.time())
    payload = {
        "sub": user_id,
        "role": role,
        "iat": now,
        "exp": now + int(cfg["session_hours"]) * 3600,
    }
    return jwt.encode(payload, cfg["jwt_secret"], algorithm="HS256")


def decode_access_token(token: str) -> dict[str, Any] | None:
    cfg = get_auth_config()
    try:
        return jwt.decode(token, cfg["jwt_secret"], algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
