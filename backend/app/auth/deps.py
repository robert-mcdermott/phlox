"""FastAPI auth dependencies: current user + admin gate.

When ``auth.enabled`` is false the app runs single-user: a synthetic local admin is returned
so every feature keeps working without login (handy for dev). When enabled, a valid
Phlox JWT (Authorization: Bearer) is required.
"""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.auth.security import decode_access_token
from app.config import get_auth_config
from app.database import get_db
from app.models import User

# Stable id used for all data when auth is disabled (single-user dev mode).
LOCAL_USER_ID = "local"


def _dev_admin() -> User:
    u = User(id=LOCAL_USER_ID, username="local", display_name="Local User", role="admin")
    u.is_active = True
    return u


def get_current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    if not get_auth_config()["enabled"]:
        return _dev_admin()

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Not authenticated")
    payload = decode_access_token(authorization.split(" ", 1)[1])
    if not payload:
        raise HTTPException(401, "Invalid or expired token")
    user = db.get(User, payload.get("sub"))
    if not user or not user.is_active:
        raise HTTPException(401, "User not found or inactive")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(403, "Admin privileges required")
    return user
