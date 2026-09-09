"""Serve workspace files / artifacts produced by tools."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user, require_owned_conversation
from app.database import get_db
from app.models import User
from app.workspace.manager import resolve_in_workspace, workspace_dir
from app import artifact_snapshots  # noqa: F401 — register committed message-file cleanup

router = APIRouter(prefix="/api/files", tags=["files"])

# Internal/noise directories never shown in the workspace files listing.
_HIDDEN_DIRS = {".git", ".phlox", ".hutchchat", "__pycache__", "node_modules", ".venv", ".cache"}


@router.get('/{conversation_id}/saved/{message_id}/{index}')
def saved_file(conversation_id: str, message_id: str, index: int,
               db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from app.models import Message
    from app.config import ATTACHMENTS_DIR
    require_owned_conversation(db, conversation_id, user)
    message = db.get(Message, message_id)
    if not message or message.conversation_id != conversation_id:
        raise HTTPException(404, 'Saved artifact not found')
    artifact = next((a for a in message.artifacts or [] if a.get('snapshot_status') == 'saved'
                     and a.get('snapshot_index') == index), None)
    path = ATTACHMENTS_DIR / message_id / f'artifact-{index}'
    if artifact is None or not path.is_file():
        raise HTTPException(404, 'Saved artifact not found')
    return FileResponse(str(path), filename=artifact.get('name') or 'artifact',
                        headers={'X-Content-Type-Options': 'nosniff'})


@router.get("/{conversation_id}")
def get_file(
    conversation_id: str,
    path: str = Query(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_owned_conversation(db, conversation_id, user)
    try:
        p = resolve_in_workspace(conversation_id, path)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if not p.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(str(p), filename=p.name)


@router.get("/{conversation_id}/list")
def list_files(
    conversation_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_owned_conversation(db, conversation_id, user)
    root = workspace_dir(conversation_id)
    files = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if any(part in _HIDDEN_DIRS for part in rel.parts):
            continue
        files.append({"path": str(rel), "name": p.name, "size": p.stat().st_size, "ext": p.suffix.lower()})
    return {"files": files}
