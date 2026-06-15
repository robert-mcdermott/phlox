"""Serve user-attached message images."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.config import ATTACHMENTS_DIR

router = APIRouter(prefix="/api/attachments", tags=["attachments"])


@router.get("/{message_id}/{idx}")
def get_attachment(message_id: str, idx: int):
    msg_dir = ATTACHMENTS_DIR / message_id
    if msg_dir.is_dir():
        for f in msg_dir.glob(f"{idx}.*"):
            return FileResponse(str(f))
    raise HTTPException(404, "Attachment not found")
