"""Each conversation gets an isolated working directory under data/workspaces/<id>/.

Filesystem, shell, and code tools operate *inside* this directory. ``resolve_in_workspace``
guards against path traversal so a tool cannot escape the workspace root.
"""
from __future__ import annotations

from pathlib import Path

from app.config import WORKSPACES_DIR


def workspace_dir(conversation_id: str) -> Path:
    """Return (creating if needed) the workspace dir for a conversation."""
    d = WORKSPACES_DIR / conversation_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def resolve_in_workspace(conversation_id: str, relative_path: str) -> Path:
    """Resolve ``relative_path`` within the workspace, rejecting traversal escapes.

    Backslashes are normalized to ``/`` first so a Windows-style traversal payload
    (e.g. ``..\\..\\windows``) is rejected on every host — on Linux ``\\`` is an ordinary
    filename character, so without this the guard's behavior would be OS-dependent.
    """
    root = workspace_dir(conversation_id).resolve()
    normalized = (relative_path or ".").replace("\\", "/")
    candidate = (root / normalized).resolve()
    if root != candidate and root not in candidate.parents:
        raise ValueError(f"Path escapes workspace: {relative_path!r}")
    return candidate
