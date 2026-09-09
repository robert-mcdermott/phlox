"""Conversations CRUD + message history (scoped to the current user)."""
from __future__ import annotations

import shutil

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user, require_owned_conversation
from app.config import WORKSPACES_DIR
from app.database import get_db
from app.models import Conversation, User
from app import runs, branches
from app.schemas import (
    ConversationCreate,
    ConversationDetail,
    ConversationOut,
    ConversationUpdate,
)

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


def _visible(query, user: User):
    """Conversations are PRIVATE to their creator — admins included. There is no bypass:
    nobody (not even an admin) can list another user's conversations."""
    return query.filter(Conversation.user_id == user.id)


def _owned(db: Session, conversation_id: str, user: User) -> Conversation:
    return require_owned_conversation(db, conversation_id, user)


@router.get("", response_model=list[ConversationOut])
def list_conversations(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    q = _visible(db.query(Conversation), user)
    rows = q.order_by(Conversation.updated_at.desc()).all()
    from app.models import Run
    statuses = dict(db.query(Run.conversation_id, Run.status).filter(
        Run.user_id == user.id, Run.active_conversation_id.is_not(None)).all())
    return [{**ConversationOut.model_validate(row).model_dump(), "run_status": statuses.get(row.id)} for row in rows]


@router.post("", response_model=ConversationDetail)
def create_conversation(
    body: ConversationCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    from app import projects
    if body.project_id:
        projects.owned(db, body.project_id, user.id, active=True)
    conv = Conversation(
        title=body.title or "New chat", profile=body.profile, model=body.model, user_id=user.id,
        project_id=body.project_id,
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return branches.detail(conv)


@router.get("/{conversation_id}", response_model=ConversationDetail)
def get_conversation(
    conversation_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    return branches.detail(_owned(db, conversation_id, user))


@router.patch("/{conversation_id}", response_model=ConversationOut)
def update_conversation(
    conversation_id: str, body: ConversationUpdate,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    with runs.LOCK:
        conv = _owned(db, conversation_id, user)
        runs.require_idle(db, conversation_id)
        from app import projects, approvals
        approvals.require_no_approval(db, conversation_id)
        if body.project_id:
            projects.owned(db, body.project_id, user.id, active=True)
        old_project = conv.project_id
        old_membership = (conv.params or {}).get('project_membership')
        for field, value in body.model_dump(exclude_unset=True).items():
            setattr(conv, field, value)
        if conv.project_id != old_project:
            import uuid
            old_membership = uuid.uuid4().hex
        if old_membership:
            conv.params = {**(conv.params or {}), 'project_membership': old_membership}
        db.commit()
        db.refresh(conv)
        return conv


@router.delete("/{conversation_id}/messages/{message_id}")
def truncate_from_message(
    conversation_id: str, message_id: str,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    """Delete a message and every message after it (for edit / regenerate)."""
    with runs.LOCK:
        conv = _owned(db, conversation_id, user)
        runs.require_idle(db, conversation_id)
        from app.models import Message
        from app.approvals import require_no_approval

        require_no_approval(db, conv.id)
        branches.initialize(conv)
        target = db.get(Message, message_id)
        if not target or target.conversation_id != conv.id:
            raise HTTPException(404, "Message not found")
        ids = {target.id}
        for _ in range(len(conv.messages)):
            expanded = ids | {m.id for m in conv.messages if m.parent_id in ids}
            if expanded == ids:
                break
            ids = expanded
        selected = conv.active_leaf_id in ids
        parent = target.parent_id
        for m in list(conv.messages):
            if m.id in ids:
                db.delete(m)
        conv.branch_choices = {k: v for k, v in (conv.branch_choices or {}).items() if k not in ids and v not in ids}
        if selected:
            conv.active_leaf_id = parent
            if parent is None:
                remaining = [m for m in conv.messages if m.id not in ids]
                conv.active_leaf_id = remaining[-1].id if remaining else None
        db.commit()
        return {"deleted": len(ids)}


@router.post("/{conversation_id}/alternatives/{message_id}", response_model=ConversationDetail)
def select_alternative(conversation_id: str, message_id: str, body: dict,
                       db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    with runs.LOCK:
        conv = _owned(db, conversation_id, user)
        runs.require_idle(db, conv.id)
        from app.approvals import require_no_approval
        require_no_approval(db, conv.id)
        if body.get('expected_leaf_id') != conv.active_leaf_id:
            raise HTTPException(409, 'The selected conversation changed. Reload before switching alternatives.')
        branches.choose(conv, message_id)
        db.commit()
        return branches.detail(conv)



@router.delete("/{conversation_id}")
def delete_conversation(
    conversation_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    with runs.LOCK:
        conv = _owned(db, conversation_id, user)
        runs.require_deletable(db, conversation_id)
        db.delete(conv)
        db.commit()
        ws = WORKSPACES_DIR / conversation_id
        # Tear down any per-conversation remote sandbox session before removing the local
        # workspace. No-op for the local/container runners; the remote runner uses this to
        # release its session deterministically (keyed by the workspace dir name).
        try:
            from app.sandbox.runner import get_runner

            get_runner().close_session(ws)
        except Exception:  # noqa: BLE001
            pass
        if ws.exists():
            shutil.rmtree(ws, ignore_errors=True)
        return {"deleted": conversation_id}
