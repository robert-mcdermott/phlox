"""Workspace checkpoint listing/restoring for a conversation."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.workspace import checkpoints
from app.workspace.manager import workspace_dir

router = APIRouter(prefix="/api/checkpoints", tags=["checkpoints"])


class RestoreIn(BaseModel):
    sha: str


@router.get("/{conversation_id}")
def list_checkpoints(conversation_id: str):
    return {"checkpoints": checkpoints.list_checkpoints(workspace_dir(conversation_id))}


@router.post("/{conversation_id}/restore")
def restore(conversation_id: str, body: RestoreIn):
    ok = checkpoints.restore_checkpoint(workspace_dir(conversation_id), body.sha)
    if not ok:
        raise HTTPException(400, f"Could not restore {body.sha}")
    return {"restored": body.sha}
