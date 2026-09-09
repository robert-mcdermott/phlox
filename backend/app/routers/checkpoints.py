"""Workspace checkpoint listing/restoring for a conversation."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user, require_owned_conversation
from app.database import get_db
from app.models import User
from app import branches
from app.workspace import checkpoints
from app.workspace.manager import workspace_dir

router = APIRouter(prefix="/api/checkpoints", tags=["checkpoints"])


class RestoreIn(BaseModel):
    sha: str


@router.get("/{conversation_id}")
def list_checkpoints(
    conversation_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_owned_conversation(db, conversation_id, user)
    return {"checkpoints": checkpoints.list_checkpoints(workspace_dir(conversation_id))}


@router.post("/{conversation_id}/restore")
@branches.serialized
def restore(
    conversation_id: str,
    body: RestoreIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_owned_conversation(db, conversation_id, user)
    from app.runs import require_idle
    from app.approvals import require_no_approval
    require_idle(db, conversation_id)
    require_no_approval(db, conversation_id)
    ok = checkpoints.restore_checkpoint(workspace_dir(conversation_id), body.sha)
    if not ok:
        raise HTTPException(400, f"Could not restore {body.sha}")
    return {"restored": body.sha}
