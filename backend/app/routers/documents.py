"""Document upload, ingestion, and management for RAG."""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user, require_admin
from app.config import UPLOADS_DIR
from app.database import get_db
from app.models import Conversation, Document, User
from app.rag import jobs
from app.rag.parsing import MAX_BYTES

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
    ingestion: dict | None = None

    class Config:
        from_attributes = True


async def save_upload(db, file, **scope):
    # Use a safe basename and reserve a durable upload row before accepting bytes.
    from app.runs import LOCK
    filename = re.split(r'[/\\]', file.filename or 'upload')[-1][:240] or 'upload'
    with LOCK:
        if db.query(Document).filter(Document.status.in_(jobs.ACTIVE)).count() >= 32:
            raise HTTPException(429, 'Document queue is full.')
        doc = Document(filename=filename, mime=file.content_type, status='pending', **scope)
        db.add(doc)
        db.commit()
    doc_id = doc.id
    dest = UPLOADS_DIR / f'{doc_id}_{filename}'
    try:
        size = 0
        with dest.open('wb') as output:
            while block := await file.read(65536):
                size += len(block)
                if size > MAX_BYTES:
                    raise HTTPException(413, 'Document exceeds the 20 MiB limit.')
                output.write(block)
        doc.size_bytes = size
        db.commit()
        jobs.enqueue(db, doc)
        return doc
    except BaseException:
        db.rollback()
        with LOCK:
            current = db.get(Document, doc_id, populate_existing=True)
            if current:
                db.delete(current)
                db.commit()
        dest.unlink(missing_ok=True)
        raise


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
    file: UploadFile,
    conversation_id: str | None = Form(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _ensure_owned_conversation(db, conversation_id, user)
    return await save_upload(db, file, conversation_id=conversation_id or None, user_id=user.id)


@router.post("/reindex")
def reindex(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    """Explicitly re-embed all ready documents and publish a new index. Admin-only."""
    return jobs.queue_rebuild(db)


@router.get('/index-status')
def index_status(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    from app.rag.maintenance import status
    return status(db)


@router.post('/{document_id}/retry', response_model=DocumentOut)
def retry(document_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from app.runs import LOCK
    with LOCK:
        doc = db.get(Document, document_id)
        if not doc or doc.user_id != user.id:
            raise HTTPException(404, 'Document not found')
        return jobs.enqueue(db, doc)


@router.delete("/{document_id}")
def delete_document(
    document_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    from app.runs import LOCK

    with LOCK:
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
