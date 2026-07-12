"""Microsoft Entra ID OIDC authorization-code flow with state, nonce, and PKCE."""
from __future__ import annotations

import base64
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import jwt
from sqlalchemy.orm import Session

from app.auth import service
from app.config import get_auth_config
from app.models import AuthHandoff, OAuthLoginState, User

logger = logging.getLogger(__name__)
STATE_TTL_MINUTES = 10
HANDOFF_TTL_MINUTES = 2


class EntraFlowError(RuntimeError):
    """Safe, expected SSO rejection surfaced without upstream response details."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _b64url_sha256(value: str) -> str:
    digest = hashlib.sha256(value.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def entra_config() -> dict[str, Any]:
    return get_auth_config().get("entra", {}) or {}


def is_enabled() -> bool:
    c = entra_config()
    tenant = str(c.get("tenant_id") or "").strip().lower()
    return bool(
        tenant
        and tenant not in {"common", "organizations", "consumers"}
        and c.get("client_id")
        and c.get("redirect_uri")
    )


def _issuer(c: dict[str, Any]) -> str:
    return f"https://login.microsoftonline.com/{c['tenant_id']}/v2.0"


def begin_login(db: Session) -> str:
    """Persist one-use browser state and return the Entra authorization URL."""
    c = entra_config()
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    now = _now()
    db.query(OAuthLoginState).filter(OAuthLoginState.expires_at < now).delete()
    db.add(
        OAuthLoginState(
            state_hash=_hash(state),
            nonce=nonce,
            code_verifier=verifier,
            expires_at=now + timedelta(minutes=STATE_TTL_MINUTES),
        )
    )
    db.commit()
    params = {
        "client_id": c["client_id"],
        "response_type": "code",
        "redirect_uri": c["redirect_uri"],
        "response_mode": "query",
        "scope": "openid profile email",
        "state": state,
        "nonce": nonce,
        "code_challenge": _b64url_sha256(verifier),
        "code_challenge_method": "S256",
    }
    return (
        f"https://login.microsoftonline.com/{c['tenant_id']}/oauth2/v2.0/authorize?"
        f"{urlencode(params)}"
    )


def _consume_state(db: Session, state: str) -> OAuthLoginState:
    now = _now()
    row = (
        db.query(OAuthLoginState)
        .filter(OAuthLoginState.state_hash == _hash(state), OAuthLoginState.expires_at >= now)
        .first()
    )
    if row is None:
        raise EntraFlowError("The SSO request is invalid, expired, or already used.")
    # Consume before exchanging the code, making callback replay fail even when the
    # upstream token request times out or returns an error.
    db.delete(row)
    db.commit()
    return row


def _validated_claims(id_token: str, nonce: str, c: dict[str, Any]) -> dict[str, Any]:
    try:
        key_client = jwt.PyJWKClient(
            f"https://login.microsoftonline.com/{c['tenant_id']}/discovery/v2.0/keys"
        )
        signing_key = key_client.get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=c["client_id"],
            issuer=_issuer(c),
            options={"require": ["exp", "iat", "iss", "aud", "sub", "nonce", "tid"]},
        )
    except jwt.PyJWTError as exc:
        raise EntraFlowError("Microsoft identity token validation failed.") from exc
    if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise EntraFlowError("Microsoft identity token nonce validation failed.")
    if str(claims.get("tid", "")).lower() != str(c["tenant_id"]).lower():
        raise EntraFlowError("Microsoft identity token tenant validation failed.")
    return claims


def _upsert_user(db: Session, claims: dict[str, Any]) -> User:
    external_id = str(claims.get("sub") or "").strip()
    email = str(claims.get("email") or claims.get("preferred_username") or "").strip() or None
    username = email or external_id
    if not external_id or not username:
        raise EntraFlowError("Microsoft identity is missing required account claims.")
    name = str(claims.get("name") or email or external_id)
    department = claims.get("department") or claims.get("companyName")

    user = service.get_by_external_id(db, "entra", external_id)
    if user is None:
        # Never silently bind an SSO identity to an existing local/different SSO account.
        collision_query = db.query(User).filter(User.username == username)
        collision = collision_query.first()
        if collision is None and email:
            collision = db.query(User).filter(User.email == email).first()
        if collision is not None:
            raise EntraFlowError("This Microsoft identity conflicts with an existing account.")
        user = service.create_user(
            db,
            username=username,
            role="user",
            email=email,
            display_name=name,
            auth_provider="entra",
            external_id=external_id,
            department=department,
        )
    elif department and user.department != department:
        user.department = department
        db.commit()
    return user


def exchange_code_and_upsert(db: Session, code: str, state: str) -> User:
    """Consume state, exchange the code, fully validate the ID token, and upsert."""
    import httpx

    saved = _consume_state(db, state)
    c = entra_config()
    try:
        token_resp = httpx.post(
            f"https://login.microsoftonline.com/{c['tenant_id']}/oauth2/v2.0/token",
            data={
                "client_id": c["client_id"],
                "client_secret": c.get("client_secret", ""),
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": c["redirect_uri"],
                "scope": "openid profile email",
                "code_verifier": saved.code_verifier,
            },
            timeout=30,
        )
        token_resp.raise_for_status()
        id_token = token_resp.json().get("id_token")
        if not id_token:
            raise EntraFlowError("Microsoft did not return an identity token.")
        claims = _validated_claims(id_token, saved.nonce, c)
    except EntraFlowError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("Entra token exchange failed: %s", type(exc).__name__)
        raise EntraFlowError("Microsoft sign-in could not be completed.") from exc
    return _upsert_user(db, claims)


def create_handoff(db: Session, user_id: str) -> str:
    code = secrets.token_urlsafe(32)
    now = _now()
    db.query(AuthHandoff).filter(AuthHandoff.expires_at < now).delete()
    db.add(
        AuthHandoff(
            code_hash=_hash(code),
            user_id=user_id,
            expires_at=now + timedelta(minutes=HANDOFF_TTL_MINUTES),
        )
    )
    db.commit()
    return code


def consume_handoff(db: Session, code: str) -> User:
    now = _now()
    row = (
        db.query(AuthHandoff)
        .filter(AuthHandoff.code_hash == _hash(code), AuthHandoff.expires_at >= now)
        .first()
    )
    user = db.get(User, row.user_id) if row else None
    if row is None or user is None or not user.is_active:
        raise EntraFlowError("The sign-in handoff is invalid, expired, or already used.")
    db.delete(row)
    db.commit()
    return user
