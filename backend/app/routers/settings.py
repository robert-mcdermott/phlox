"""Per-user runtime settings (active profile, params, theme, system prompt)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.database import get_db
from app.models import User
from app.runtime_settings import get_settings, update_settings
from app.schemas import SettingsOut, SettingsUpdate

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("", response_model=SettingsOut)
def read_settings(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return get_settings(db, user.id)


@router.patch("", response_model=SettingsOut)
def patch_settings(
    body: SettingsUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    return update_settings(db, body.model_dump(exclude_unset=True), user.id)
