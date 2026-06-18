"""API key generation, hashing, and lookup for the OpenAI-compatible gateway.

A Phlox API key looks like ``phlox-sk-<43 url-safe base64 chars>``. Only its **SHA-256
hash** is persisted (``ApiKey.key_hash``); the plaintext is returned to the caller exactly
once at creation and is unrecoverable afterwards. Resolving a presented key is a single
indexed hash lookup, so there is no per-request bcrypt cost on the hot gateway path (these
are high-entropy random secrets, not user-chosen passwords, so a fast hash is appropriate).

The owning ``user_id`` is the **billable identity**: gateway usage is attributed to that
user (and, via the ledger's write-time snapshot, their department) exactly like interactive
chat usage.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import ApiKey

# Human-recognizable, OpenAI-style prefix so keys are easy to spot in logs/config.
KEY_PREFIX = "phlox-sk-"
# Number of leading characters (incl. the prefix) kept as a non-secret display label.
DISPLAY_PREFIX_LEN = len(KEY_PREFIX) + 4


def hash_key(plaintext: str) -> str:
    """Return the hex SHA-256 of a key. Used both at creation and on every lookup."""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def generate_key() -> tuple[str, str, str]:
    """Mint a new key. Returns ``(plaintext, key_hash, display_prefix)``.

    ``plaintext`` is shown to the user once; only ``key_hash`` is stored.
    """
    secret = f"{KEY_PREFIX}{secrets.token_urlsafe(32)}"
    return secret, hash_key(secret), secret[:DISPLAY_PREFIX_LEN]


def create_key(
    db: Session,
    *,
    user_id: str,
    name: str | None = None,
    expires_at: datetime | None = None,
    scopes: list[str] | None = None,
) -> tuple[ApiKey, str]:
    """Create and persist a key for ``user_id``; return ``(row, plaintext_secret)``.

    The plaintext is the caller's only chance to see the full key.
    """
    plaintext, key_hash, prefix = generate_key()
    row = ApiKey(
        user_id=user_id,
        name=(name or "API key").strip()[:150] or "API key",
        key_hash=key_hash,
        prefix=prefix,
        scopes=scopes or None,
        expires_at=expires_at,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row, plaintext


def resolve_key(db: Session, plaintext: str) -> ApiKey | None:
    """Resolve a presented key to its active, unexpired ``ApiKey`` row, or None.

    Updates ``last_used_at`` (best-effort) on a successful resolve so the UI can show
    recency. An inactive (revoked) or expired key resolves to None.
    """
    if not plaintext:
        return None
    row = db.query(ApiKey).filter(ApiKey.key_hash == hash_key(plaintext)).first()
    if row is None or not row.is_active:
        return None
    if row.expires_at is not None:
        exp = row.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp <= datetime.now(timezone.utc):
            return None
    row.last_used_at = datetime.now(timezone.utc)
    try:
        db.commit()
    except Exception:  # noqa: BLE001 — last_used_at is non-critical; never fail a request on it.
        db.rollback()
    return row


def list_keys(db: Session, user_id: str) -> list[ApiKey]:
    return (
        db.query(ApiKey)
        .filter(ApiKey.user_id == user_id)
        .order_by(ApiKey.created_at.desc())
        .all()
    )


def revoke_key(db: Session, user_id: str, key_id: str) -> bool:
    """Soft-revoke a key the user owns (keeps the audit row). Returns True if revoked."""
    row = db.get(ApiKey, key_id)
    if row is None or row.user_id != user_id:
        return False
    row.is_active = False
    db.commit()
    return True
