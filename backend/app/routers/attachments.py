"""Serve user-attached message images."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.config import ATTACHMENTS_DIR
from app.database import get_db
from app.models import Conversation, Message, User

router = APIRouter(prefix="/api/attachments", tags=["attachments"])


@router.get("/{message_id}/{idx}")
def get_attachment(
    message_id: str,
    idx: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    message = db.get(Message, message_id)
    conversation = db.get(Conversation, message.conversation_id) if message else None
    if message is None or conversation is None or conversation.user_id != user.id:
        raise HTTPException(404, "Attachment not found")
    msg_dir = ATTACHMENTS_DIR / message_id
    if msg_dir.is_dir():
        for f in msg_dir.glob(f"{idx}.*"):
            return FileResponse(str(f))
    raise HTTPException(404, "Attachment not found")
