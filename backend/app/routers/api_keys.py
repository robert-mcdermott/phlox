"""API key management for the OpenAI-compatible gateway.

    GET    /api/api-keys        — list the signed-in user's keys (metadata only, no secrets)
    POST   /api/api-keys        — mint a new key; the plaintext secret is returned **once**
    DELETE /api/api-keys/{id}   — revoke a key the user owns

Keys are per-user and scoped to their owner: a user manages only their own keys (admins
included — there is no cross-user read, consistent with the privacy model in docs/AUTH.md).
The full secret is shown exactly once at creation and is never recoverable afterwards.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import api_keys as svc
from app.auth.deps import get_current_user
from app.database import get_db
from app.models import User

router = APIRouter(prefix="/api/api-keys", tags=["api-keys"])


class ApiKeyOut(BaseModel):
    id: str
    name: str
    prefix: str
    is_active: bool
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    created_at: datetime | None = None

    class Config:
        from_attributes = True


class CreateApiKeyIn(BaseModel):
    name: str | None = None
    expires_at: datetime | None = None


class CreateApiKeyOut(ApiKeyOut):
    # The plaintext secret — returned ONLY here, at creation, and never again.
    key: str


@router.get("", response_model=list[ApiKeyOut])
def list_api_keys(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return svc.list_keys(db, user.id)


@router.post("", response_model=CreateApiKeyOut)
def create_api_key(
    body: CreateApiKeyIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row, plaintext = svc.create_key(
        db, user_id=user.id, name=body.name, expires_at=body.expires_at
    )
    return CreateApiKeyOut(
        id=row.id,
        name=row.name,
        prefix=row.prefix,
        is_active=row.is_active,
        expires_at=row.expires_at,
        last_used_at=row.last_used_at,
        created_at=row.created_at,
        key=plaintext,
    )


@router.delete("/{key_id}")
def revoke_api_key(
    key_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    # 404 (not 403) on a key the user doesn't own, so existence isn't leaked (per AUTH.md).
    if not svc.revoke_key(db, user.id, key_id):
        raise HTTPException(404, "API key not found")
    return {"revoked": key_id}
