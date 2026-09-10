"""Per-user runtime settings (active profile, params, theme, system prompt)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import config
from app.auth.deps import get_current_user
from app.database import get_db
from app.models import User
from app.runtime_settings import get_settings, update_settings
from app.schemas import SettingsOut, SettingsUpdate

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("/suggestions")
def read_suggestions(_: User = Depends(get_current_user)):
    """Welcome-screen prompt suggestions (deployment-wide, admin-editable — see
    routers/admin_config.py). Readable by every signed-in user."""
    return {"suggestions": config.get_suggestions()}


@router.get('/research')
def read_research(_: User = Depends(get_current_user)):
    """Only public numerical presets; never deployment credentials or other users' data."""
    from app.sources import MAX_TURN_SOURCES, MAX_CONVERSATION_SOURCES
    return {'presets': config.get_research_config(), 'source_limit': MAX_TURN_SOURCES,
            'conversation_source_limit': MAX_CONVERSATION_SOURCES}


@router.get("", response_model=SettingsOut)
def read_settings(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return get_settings(db, user.id)


@router.patch("", response_model=SettingsOut)
def patch_settings(
    body: SettingsUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    return update_settings(db, body.model_dump(exclude_unset=True), user.id)
