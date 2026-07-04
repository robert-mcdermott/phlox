"""Document upload, ingestion, and management for RAG."""
from __future__ import annotations

import shutil

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user, require_admin
from app.config import UPLOADS_DIR
from app.database import SessionLocal, get_db
from app.models import Conversation, Document, User
from app.rag.ingest import ingest_document

router = APIRouter(prefix="/api/documents", tags=["documents"])


class DocumentOut(BaseModel):
    id: str
    filename: str
    conversation_id: str | None = None
    mime: str | None = None
    size_bytes: int
    n_chunks: int
    status: str
    error: str | None = None

    class Config:
        from_attributes = True


def _ingest_job(document_id: str, path_str: str):
    """Background ingestion with its own DB session."""
    from pathlib import Path

    db = SessionLocal()
    try:
        doc = db.get(Document, document_id)
        if doc:
            ingest_document(db, doc, Path(path_str))
    finally:
        db.close()


def _owned_docs(db: Session, user: User):
    """Documents are private to their owner — admins included (no bypass)."""
    return db.query(Document).filter(Document.user_id == user.id)


def _ensure_owned_conversation(db: Session, conversation_id: str | None, user: User) -> None:
    if not conversation_id:
        return
    conv = db.get(Conversation, conversation_id)
    if not conv or conv.user_id != user.id:
        raise HTTPException(404, "Conversation not found")


@router.get("", response_model=list[DocumentOut])
def list_documents(
    conversation_id: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = _owned_docs(db, user)
    if conversation_id:
        # That conversation's scoped docs plus the user's global KB.
        q = q.filter(
            (Document.conversation_id == conversation_id) | (Document.conversation_id.is_(None))
        )
    return q.order_by(Document.created_at.desc()).all()


@router.post("", response_model=DocumentOut)
async def upload_document(
    background: BackgroundTasks,
    file: UploadFile,
    conversation_id: str | None = Form(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _ensure_owned_conversation(db, conversation_id, user)
    doc = Document(
        filename=file.filename or "upload",
        mime=file.content_type,
        status="pending",
        conversation_id=conversation_id or None,
        user_id=user.id,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    dest = UPLOADS_DIR / f"{doc.id}_{doc.filename}"
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    doc.size_bytes = dest.stat().st_size
    db.commit()
    db.refresh(doc)

    background.add_task(_ingest_job, doc.id, str(dest))
    return doc


@router.post("/reindex")
def reindex(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    """Re-embed (if the embedder changed) and rebuild the vector index. Admin-only."""
    from app.rag.maintenance import sync_index

    return sync_index(db)


@router.delete("/{document_id}")
def delete_document(
    document_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    doc = db.get(Document, document_id)
    if not doc or doc.user_id != user.id:
        raise HTTPException(404, "Document not found")
    db.delete(doc)
    db.commit()
    for p in UPLOADS_DIR.glob(f"{document_id}_*"):
        p.unlink(missing_ok=True)
    # Remove the document's vectors from the index.
    try:
        from app.rag.store import get_vector_store

        get_vector_store().delete_by_document(document_id)
    except Exception:  # noqa: BLE001
        pass
    return {"deleted": document_id}
