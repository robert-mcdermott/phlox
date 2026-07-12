"""Conversations CRUD + message history (scoped to the current user)."""
from __future__ import annotations

import shutil

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user, require_owned_conversation
from app.config import WORKSPACES_DIR
from app.database import get_db
from app.models import Conversation, User
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
    return q.order_by(Conversation.updated_at.desc()).all()


@router.post("", response_model=ConversationDetail)
def create_conversation(
    body: ConversationCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    conv = Conversation(
        title=body.title or "New chat", profile=body.profile, model=body.model, user_id=user.id
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv


@router.get("/{conversation_id}", response_model=ConversationDetail)
def get_conversation(
    conversation_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    return _owned(db, conversation_id, user)


@router.patch("/{conversation_id}", response_model=ConversationOut)
def update_conversation(
    conversation_id: str, body: ConversationUpdate,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    conv = _owned(db, conversation_id, user)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(conv, field, value)
    db.commit()
    db.refresh(conv)
    return conv


@router.delete("/{conversation_id}/messages/{message_id}")
def truncate_from_message(
    conversation_id: str, message_id: str,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    """Delete a message and every message after it (for edit / regenerate)."""
    from app.models import Message

    conv = _owned(db, conversation_id, user)
    target = db.get(Message, message_id)
    if not target or target.conversation_id != conv.id:
        raise HTTPException(404, "Message not found")
    deleted = 0
    for m in list(conv.messages):
        if m.created_at >= target.created_at:
            db.delete(m)
            deleted += 1
    db.commit()
    return {"deleted": deleted}


@router.delete("/{conversation_id}")
def delete_conversation(
    conversation_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    conv = _owned(db, conversation_id, user)
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
