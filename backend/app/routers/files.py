"""Serve workspace files / artifacts produced by tools."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from app.workspace.manager import resolve_in_workspace, workspace_dir

router = APIRouter(prefix="/api/files", tags=["files"])

# Internal/noise directories never shown in the workspace files listing.
_HIDDEN_DIRS = {".git", ".phlox", ".hutchchat", "__pycache__", "node_modules", ".venv", ".cache"}


@router.get("/{conversation_id}")
def get_file(conversation_id: str, path: str = Query(...)):
    try:
        p = resolve_in_workspace(conversation_id, path)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if not p.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(str(p), filename=p.name)


@router.get("/{conversation_id}/list")
def list_files(conversation_id: str):
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
