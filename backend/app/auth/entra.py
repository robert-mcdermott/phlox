"""Microsoft Entra ID (Azure AD) OIDC integration — production SSO.

This is the SSO seam. It's wired and follows the standard OIDC authorization-code flow, but
is inert until ``auth.entra`` is configured in ``config.yml`` with ``tenant_id``,
``client_id``, ``client_secret`` and a ``redirect_uri``. On success it upserts a local
``User`` (auth_provider="entra") and the app issues its own JWT, so the rest of the system
is identical to local auth. See ``docs/AUTH.md`` for setup + testing notes.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.auth import service
from app.config import get_auth_config
from app.models import User

logger = logging.getLogger(__name__)


def entra_config() -> dict[str, Any]:
    return get_auth_config().get("entra", {}) or {}


def is_enabled() -> bool:
    c = entra_config()
    return bool(c.get("tenant_id") and c.get("client_id"))


def authorize_url(state: str) -> str:
    c = entra_config()
    tenant = c["tenant_id"]
    from urllib.parse import urlencode

    params = {
        "client_id": c["client_id"],
        "response_type": "code",
        "redirect_uri": c["redirect_uri"],
        "response_mode": "query",
        "scope": "openid profile email",
        "state": state,
    }
    return f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize?{urlencode(params)}"


def exchange_code_and_upsert(db: Session, code: str) -> User:
    """Exchange an auth code for tokens, read the profile, and upsert the user."""
    import httpx

    c = entra_config()
    tenant = c["tenant_id"]
    token_resp = httpx.post(
        f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        data={
            "client_id": c["client_id"],
            "client_secret": c.get("client_secret", ""),
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": c["redirect_uri"],
            "scope": "openid profile email",
        },
        timeout=30,
    )
    token_resp.raise_for_status()
    access_token = token_resp.json()["access_token"]

    me = httpx.get(
        "https://graph.microsoft.com/oidc/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    me.raise_for_status()
    claims = me.json()
    external_id = claims.get("sub")
    email = claims.get("email")
    name = claims.get("name") or email or external_id
    # Department for chargeback, if the tenant emits it. Not in the default OIDC scope —
    # surfaces when an optional claim / directory extension is configured; fall back to the
    # company name. Harmless (None) otherwise.
    department = claims.get("department") or claims.get("companyName")

    user = service.get_by_external_id(db, "entra", external_id)
    if user is None:
        # Default new SSO users to the "user" role; promote in the admin panel.
        user = service.create_user(
            db,
            username=email or external_id,
            role="user",
            email=email,
            display_name=name,
            auth_provider="entra",
            external_id=external_id,
            department=department,
        )
    elif department and user.department != department:
        # Keep the department fresh from SSO (e.g. the user transferred). Snapshotting into
        # the ledger means past usage stays billed to the department active at the time.
        user.department = department
        db.commit()
    return user
