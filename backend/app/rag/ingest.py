"""Parse uploaded files into text, chunk, embed, and store as DocChunks."""
from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import DocChunk, Document
from app.rag.embed import embed_texts

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1200  # characters
CHUNK_OVERLAP = 150


def _payload(document, chunk) -> dict:
    """Qdrant payload for a chunk (scope keys omitted when empty so IsEmpty matches)."""
    from app.rag.retrieve import build_chunk_payload

    return build_chunk_payload(
        chunk,
        document.filename,
        document.conversation_id,
        document.user_id,
        document.assistant_id,
    )
TEXT_EXTS = {".txt", ".md", ".markdown", ".py", ".js", ".ts", ".json", ".csv", ".html", ".xml", ".yaml", ".yml"}


def extract_text(path: Path, mime: str | None) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":
        return _extract_pdf(path)
    if ext == ".docx":
        return _extract_docx(path)
    if ext in TEXT_EXTS or (mime and mime.startswith("text/")):
        return path.read_text(encoding="utf-8", errors="replace")
    # Last resort: try as text.
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _extract_pdf(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return "\n\n".join((page.extract_text() or "") for page in reader.pages)


def _extract_docx(path: Path) -> str:
    import docx

    d = docx.Document(str(path))
    return "\n".join(p.text for p in d.paragraphs)


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    text = text.strip()
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        chunks.append(text[start:end])
        if end == len(text):
            break
        start = end - overlap
    return chunks


def ingest_document(db: Session, document: Document, file_path: Path) -> None:
    """Parse → chunk → embed → persist. Updates document.status in place."""
    try:
        text = extract_text(file_path, document.mime)
        chunks = chunk_text(text)
        if not chunks:
            document.status = "error"
            document.error = "No extractable text"
            db.commit()
            return
        vectors = embed_texts(chunks)
        chunk_rows: list[DocChunk] = []
        for i, (chunk, vec) in enumerate(zip(chunks, vectors, strict=False)):
            row = DocChunk(document_id=document.id, ordinal=i, text=chunk, embedding=vec)
            db.add(row)
            chunk_rows.append(row)
        db.flush()  # assign chunk ids before indexing

        # Index vectors in the vector store (Qdrant). SQLite stays the source of truth.
        from app.rag.embed import sparse_embed
        from app.rag.store import get_vector_store

        store = get_vector_store()
        store.ensure_collection(len(vectors[0]))
        store.upsert(
            [
                {
                    "id": row.id,
                    "dense": vec,
                    "sparse": sparse_embed(row.text),
                    "payload": _payload(document, row),
                }
                for row, vec in zip(chunk_rows, vectors, strict=False)
            ]
        )

        document.n_chunks = len(chunks)
        document.status = "ready"
        document.error = None
        db.commit()
        logger.info("Ingested %s (%d chunks)", document.filename, len(chunks))
    except Exception as e:  # noqa: BLE001
        logger.exception("Ingestion failed for %s", document.filename)
        document.status = "error"
        document.error = str(e)
        db.commit()
