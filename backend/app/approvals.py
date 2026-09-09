"""Approval snapshots and atomic claims, without automatic action replay.

A pending snapshot is resumable for 24 hours. Once claimed it is never claimable again,
even if the response disconnects or the process dies. Durable worker recovery belongs to
the run service; an unresolved claim explicitly reports an unknown outcome.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models import Conversation, PendingApproval, User

APPROVAL_TTL = timedelta(hours=24)
UNRESOLVED = {"pending", "claimed", "interrupted", "failed"}


def owned_approval(db: Session, pending_id: str, user: User) -> tuple[PendingApproval, Conversation]:
    pending = db.get(PendingApproval, pending_id)
    conversation = db.get(Conversation, pending.conversation_id) if pending else None
    if not conversation or conversation.user_id != user.id:
        raise HTTPException(404, "Approval request not found")
    return pending, conversation


def expires_at(pending: PendingApproval) -> datetime:
    return pending.created_at.replace(tzinfo=timezone.utc) + APPROVAL_TTL


def validate_resume(pending: PendingApproval, decisions: dict[str, str]) -> None:
    if pending.status != "pending":
        raise HTTPException(409, "Approval already claimed or closed. Refresh to see its status.")
    if expires_at(pending) <= datetime.now(timezone.utc):
        raise HTTPException(409, "Approval expired. Dismiss it and start a new turn.")
    state = pending.state
    if state.get("version") not in {2, 3}:
        raise HTTPException(409, "This older approval lacks execution counters. Dismiss it and start a new turn.")
    expected = [call["id"] for call in state.get("pending_calls", [])]
    if len(expected) != len(set(expected)) or set(decisions) != set(expected):
        raise HTTPException(422, "Decisions must cover exactly the pending tool-call IDs.")


def claim(db: Session, pending_id: str) -> None:
    """Compare-and-set in the database; only the winning request may dispatch tools."""
    result = db.execute(
        update(PendingApproval)
        .where(PendingApproval.id == pending_id, PendingApproval.status == "pending",
               PendingApproval.created_at > datetime.now(timezone.utc) - APPROVAL_TTL)
        .values(status="claimed")
        .execution_options(synchronize_session=False)
    )
    db.commit()
    if result.rowcount != 1:
        raise HTTPException(409, "Approval already claimed or closed. Refresh to see its status.")


def finish_claim(db: Session, pending_id: str, status: str) -> None:
    db.execute(
        update(PendingApproval)
        .where(PendingApproval.id == pending_id, PendingApproval.status == "claimed")
        .values(status=status)
        .execution_options(synchronize_session=False)
    )
    db.commit()


def require_no_approval(db: Session, conversation_id: str) -> None:
    if db.query(PendingApproval.id).filter(
        PendingApproval.conversation_id == conversation_id,
        PendingApproval.status.in_(UNRESOLVED),
    ).first():
        raise HTTPException(409, "Resolve or dismiss the existing approval before changing this conversation.")


def public_snapshot(pending: PendingApproval) -> dict:
    """Owner-only UI projection. Never return the canonical prompt/history/settings."""
    state = pending.state
    status = pending.status
    if status == "pending":
        if state.get("version") not in {2, 3}:
            status = "legacy"
        elif expires_at(pending) <= datetime.now(timezone.utc):
            status = "expired"
    # Only the in-flight assistant preamble; no system prompts or prior user history.
    content = next((m.get("content", "") for m in reversed(state.get("messages", []))
                    if m.get("role") == "assistant"), "")
    from app.research import Research

    return {
        "research": ({**Research(state=state['research']).progress(),
                      'phase': 'paused'} if state.get('research') else None),
        "sources": state.get("sources") or [],
        "pending_id": pending.id,
        "status": status,
        "expires_at": expires_at(pending).isoformat(),
        "calls": state.get("pending_calls", []),
        "content": content,
        "tool_steps": state.get("tool_steps", []),
        "artifacts": state.get("all_artifacts", []),
        "usage": state.get("usage_summary") or state.get("turn_usage"),
        "rounds_used": state.get("rounds_used"),
    }
