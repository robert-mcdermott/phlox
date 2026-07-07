"""Custom assistants: admin-curated personas (base model + system prompt + knowledge base).

Reads are open to any authenticated user (filtered to active, public-or-own assistants);
writes are admin-only for now — ``created_by`` + ``visibility`` already support opening
creation up to users later. Knowledge-base documents live in the ``documents`` table with
``assistant_id`` set and ``user_id`` NULL (deployment-owned): they survive creator deletion
and never appear in personal document listings.
"""
from __future__ import annotations

import shutil

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user, require_admin
from app.config import UPLOADS_DIR
from app.database import get_db
from app.models import Assistant, Document, User
from app.routers.documents import DocumentOut, _ingest_job
from app.schemas import AssistantCreate, AssistantOut, AssistantUpdate

router = APIRouter(prefix="/api/assistants", tags=["assistants"])


def _visible(db: Session, assistant_id: str, user: User) -> Assistant:
    a = db.get(Assistant, assistant_id)
    if not a or (a.visibility != "public" and a.created_by != user.id and user.role != "admin"):
        raise HTTPException(404, "Assistant not found")
    return a


def _doc_counts(db: Session, assistant_ids: list[str]) -> dict[str, int]:
    if not assistant_ids:
        return {}
    from sqlalchemy import func

    rows = (
        db.query(Document.assistant_id, func.count(Document.id))
        .filter(Document.assistant_id.in_(assistant_ids), Document.status == "ready")
        .group_by(Document.assistant_id)
        .all()
    )
    return dict(rows)


def _out(a: Assistant, n_documents: int) -> AssistantOut:
    out = AssistantOut.model_validate(a)
    out.n_documents = n_documents
    return out


@router.get("", response_model=list[AssistantOut])
def list_assistants(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    q = db.query(Assistant).filter(Assistant.is_active.is_(True))
    if user.role != "admin":
        # Admins see all assistants (they manage them); users see public + their own.
        q = q.filter(
            (Assistant.visibility == "public") | (Assistant.created_by == user.id)
        )
    rows = q.order_by(Assistant.created_at).all()
    counts = _doc_counts(db, [a.id for a in rows])
    return [_out(a, counts.get(a.id, 0)) for a in rows]


@router.post("", response_model=AssistantOut)
def create_assistant(
    body: AssistantCreate, db: Session = Depends(get_db), user: User = Depends(require_admin)
):
    a = Assistant(**body.model_dump(), created_by=user.id)
    db.add(a)
    db.commit()
    db.refresh(a)
    return _out(a, 0)


@router.get("/{assistant_id}", response_model=AssistantOut)
def get_assistant(
    assistant_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    a = _visible(db, assistant_id, user)
    return _out(a, _doc_counts(db, [a.id]).get(a.id, 0))


@router.patch("/{assistant_id}", response_model=AssistantOut)
def update_assistant(
    assistant_id: str,
    body: AssistantUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    a = db.get(Assistant, assistant_id)
    if not a:
        raise HTTPException(404, "Assistant not found")
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(a, key, value)
    db.commit()
    db.refresh(a)
    return _out(a, _doc_counts(db, [a.id]).get(a.id, 0))


@router.delete("/{assistant_id}")
def delete_assistant(
    assistant_id: str, db: Session = Depends(get_db), _: User = Depends(require_admin)
):
    a = db.get(Assistant, assistant_id)
    if not a:
        raise HTTPException(404, "Assistant not found")
    # Cascade the knowledge base: rows (chunks cascade via ORM), upload files, vectors.
    docs = db.query(Document).filter(Document.assistant_id == assistant_id).all()
    doc_ids = [d.id for d in docs]
    for doc in docs:
        db.delete(doc)
    db.delete(a)
    db.commit()
    for doc_id in doc_ids:
        for p in UPLOADS_DIR.glob(f"{doc_id}_*"):
            p.unlink(missing_ok=True)
        try:
            from app.rag.store import get_vector_store

            get_vector_store().delete_by_document(doc_id)
        except Exception:  # noqa: BLE001
            pass
    # Conversations keep their dangling assistant_id and degrade to the snapshot.
    return {"deleted": assistant_id, "documents_deleted": len(doc_ids)}


# -- knowledge base ----------------------------------------------------------
@router.get("/{assistant_id}/documents", response_model=list[DocumentOut])
def list_assistant_documents(
    assistant_id: str, db: Session = Depends(get_db), _: User = Depends(require_admin)
):
    if not db.get(Assistant, assistant_id):
        raise HTTPException(404, "Assistant not found")
    return (
        db.query(Document)
        .filter(Document.assistant_id == assistant_id)
        .order_by(Document.created_at.desc())
        .all()
    )


@router.post("/{assistant_id}/documents", response_model=DocumentOut)
async def upload_assistant_document(
    assistant_id: str,
    background: BackgroundTasks,
    file: UploadFile,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    if not db.get(Assistant, assistant_id):
        raise HTTPException(404, "Assistant not found")
    doc = Document(
        filename=file.filename or "upload",
        mime=file.content_type,
        status="pending",
        assistant_id=assistant_id,
        user_id=None,  # deployment-owned; see module docstring
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


@router.delete("/{assistant_id}/documents/{document_id}")
def delete_assistant_document(
    assistant_id: str,
    document_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    doc = db.get(Document, document_id)
    if not doc or doc.assistant_id != assistant_id:
        raise HTTPException(404, "Document not found")
    db.delete(doc)
    db.commit()
    for p in UPLOADS_DIR.glob(f"{document_id}_*"):
        p.unlink(missing_ok=True)
    try:
        from app.rag.store import get_vector_store

        get_vector_store().delete_by_document(document_id)
    except Exception:  # noqa: BLE001
        pass
    return {"deleted": document_id}
