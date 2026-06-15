"""Cross-conversation memory management."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import memory
from app.auth.deps import get_current_user
from app.database import get_db
from app.models import User

router = APIRouter(prefix="/api/memories", tags=["memories"])


class MemoryOut(BaseModel):
    id: str
    content: str
    kind: str
    source_conversation_id: str | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class MemoryIn(BaseModel):
    content: str
    kind: str = "fact"


@router.get("", response_model=list[MemoryOut])
def list_all(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return memory.list_memories(db, user.id)


@router.post("", response_model=MemoryOut)
def create(body: MemoryIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return memory.save_memory(db, body.content, kind=body.kind, user_id=user.id)


@router.delete("/{memory_id}")
def delete(memory_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if not memory.delete_memory(db, memory_id, user.id):
        raise HTTPException(404, "Memory not found")
    return {"deleted": memory_id}
