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
from app.models import Conversation, User

# Stable id used for all data when auth is disabled (single-user dev mode).
LOCAL_USER_ID = "local"


def _dev_admin() -> User:
    u = User(id=LOCAL_USER_ID, username="local", display_name="Local User", role="admin")
    u.is_active = True
    u.must_change_password = False
    return u


def get_authenticated_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    """Resolve a valid session, including a session restricted to password setup."""
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


def get_current_user(user: User = Depends(get_authenticated_user)) -> User:
    """Require a fully initialized account for normal application access."""
    if user.must_change_password:
        raise HTTPException(403, "Password change required")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(403, "Admin privileges required")
    return user


def require_owned_conversation(db: Session, conversation_id: str, user: User) -> Conversation:
    """Return an owned conversation without revealing whether a foreign id exists."""
    conversation = db.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != user.id:
        raise HTTPException(404, "Conversation not found")
    return conversation


def require_api_key(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    """Resolve an ``Authorization: Bearer <phlox-sk-...>`` API key to its owning user.

    This is the auth seam for the OpenAI-compatible **gateway** (``/v1/*``). It deliberately
    accepts *only* API keys (not the browser session JWT) so programmatic access is always
    attributable to a named, revocable key. Errors mirror the OpenAI spec shape
    (401 + ``error`` object) so SDKs surface them cleanly.

    When ``auth.enabled`` is false the app is single-user dev mode: a valid key is still
    required (so the gateway behaves identically in dev and prod), but it resolves against
    the synthetic local admin when the stored key has no live ``User`` row.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Missing API key. Pass 'Authorization: Bearer <key>'.")
    from app.api_keys import resolve_key

    key_row = resolve_key(db, authorization.split(" ", 1)[1].strip())
    if key_row is None:
        raise HTTPException(401, "Invalid, revoked, or expired API key.")
    user = db.get(User, key_row.user_id)
    if user is None and not get_auth_config()["enabled"]:
        return _dev_admin()
    if user is None or not user.is_active:
        raise HTTPException(401, "The user for this API key is inactive or no longer exists.")
    if user.must_change_password:
        raise HTTPException(403, "Password change required before API keys can be used.")
    return user
