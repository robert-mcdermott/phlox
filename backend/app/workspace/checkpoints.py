"""Git-based checkpointing of a conversation workspace.

Each workspace is a tiny git repo. Mutating tools auto-snapshot before they run (see the
harness), and the agent/user can list and restore snapshots. Restore updates the working
tree to a past snapshot without rewriting history, so you can always go forward again.

All operations are best-effort: if git isn't available the feature degrades silently and
the rest of the app is unaffected.
"""
from __future__ import annotations

import logging
import subprocess
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_GIT_ENV = {"GIT_AUTHOR_NAME": "Phlox", "GIT_AUTHOR_EMAIL": "agent@phlox.local",
            "GIT_COMMITTER_NAME": "Phlox", "GIT_COMMITTER_EMAIL": "agent@phlox.local"}

# One lock per workspace, serializing git operations against that workspace's repo.
# Needed now that concurrent sub-agents can mutate the same workspace in parallel
# (see spawn_subagent) — two concurrent `git add -A && git commit` calls against the
# same repo race on the index/lock file and can corrupt or fail unpredictably.
# RLock because restore_checkpoint calls create_checkpoint while already holding it.
_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


def _lock_for(workspace: Path) -> threading.RLock:
    key = str(workspace)
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.RLock()
            _locks[key] = lock
        return lock


def _git(workspace: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess:
    import os

    env = {**os.environ, **_GIT_ENV}
    return subprocess.run(
        ["git", *args], cwd=str(workspace), capture_output=True, text=True, env=env,
        check=check, timeout=30,
    )


def git_available() -> bool:
    try:
        subprocess.run(["git", "--version"], capture_output=True, timeout=10, check=True)
        return True
    except Exception:  # noqa: BLE001
        return False


def ensure_repo(workspace: Path) -> bool:
    """Initialize the workspace git repo if needed. Returns True if a repo is ready."""
    if not git_available():
        return False
    if (workspace / ".git").exists():
        return True
    try:
        _git(workspace, "init", "-q")
        _git(workspace, "commit", "--allow-empty", "-m", "init", "-q")
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("git init failed in %s: %s", workspace, e)
        return False


def create_checkpoint(workspace: Path, label: str) -> str | None:
    """Commit the current workspace state. Returns the short sha, or None if unchanged/failed."""
    with _lock_for(workspace):
        if not ensure_repo(workspace):
            return None
        try:
            _git(workspace, "add", "-A")
            status = _git(workspace, "status", "--porcelain")
            if not status.stdout.strip():
                return None  # nothing changed since last checkpoint
            _git(workspace, "commit", "-m", label, "-q")
            sha = _git(workspace, "rev-parse", "--short", "HEAD").stdout.strip()
            return sha or None
        except Exception as e:  # noqa: BLE001
            logger.warning("checkpoint failed: %s", e)
            return None


def list_checkpoints(workspace: Path) -> list[dict]:
    if not (workspace / ".git").exists():
        return []
    try:
        out = _git(workspace, "log", "--pretty=format:%h\x1f%s\x1f%cI", "-n", "50")
        rows = []
        for line in out.stdout.splitlines():
            parts = line.split("\x1f")
            if len(parts) == 3:
                rows.append({"sha": parts[0], "label": parts[1], "date": parts[2]})
        return rows
    except Exception:  # noqa: BLE001
        return []


def restore_checkpoint(workspace: Path, sha: str) -> bool:
    """Restore the working tree to a snapshot (without losing later history)."""
    with _lock_for(workspace):
        if not (workspace / ".git").exists():
            return False
        try:
            # Record current state first so nothing is lost.
            create_checkpoint(workspace, "before restore")
            _git(workspace, "restore", "--source", sha, "--worktree", "--", ".", check=True)
            create_checkpoint(workspace, f"restored {sha}")
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("restore failed: %s", e)
            return False
