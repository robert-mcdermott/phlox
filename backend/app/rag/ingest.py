"""Parse, embed and publish complete document generations; retries replace rather than append."""
from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path

from app.models import DocChunk, Document
from app.rag.embed import sparse_embed
from app.rag.identity import EmbeddingError, embed, identity
from app.rag.parsing import DocumentError, chunks, extract, MAX_BYTES, TEXT_EXTS  # noqa: F401
from app.runs import LOCK

logger = logging.getLogger(__name__)


class Interrupted(DocumentError):
    pass


def _payload(document, chunk):
    from app.rag.retrieve import build_chunk_payload
    return build_chunk_payload(chunk, document.filename, document.conversation_id, document.user_id, document.assistant_id)


def extract_text(path, mime=None):
    return '\n\n'.join(text for text, _ in extract(path, mime))


def chunk_text(text, size=1200, overlap=150):
    text = text.strip()
    return [text[n:n + size] for n in range(0, len(text), size - overlap) if n == 0 or n + overlap < len(text)]


def ingest_document(db, document, file_path: Path, stopped=lambda: False):
    doc_id = document.id
    token = (document.ingestion or {}).get('token')
    started = time.monotonic()

    def check():
        db.expire_all()
        current = db.get(Document, doc_id)
        if stopped() or not current or current.status != 'processing' or (current.ingestion or {}).get('token') != token:
            raise Interrupted('Processing interrupted. Retry to start a new attempt.')
        if time.monotonic() - started > 300:
            raise DocumentError('Processing exceeded five minutes. Retry a smaller document.')
        return current

    def progress(stage, completed=0, total=0):
        with LOCK:
            current = check()
            current.ingestion = {**current.ingestion, 'stage': stage, 'completed': completed, 'total': total}
            db.commit()

    try:
        if file_path.stat().st_size > MAX_BYTES:
            raise DocumentError('Document exceeds the 20 MiB limit.')
        progress('extracting')
        passages, digest = chunks(file_path, document.mime, check)
        spec = identity()
        # Never insert vectors of a new identity into the last good index.
        existing = db.query(DocChunk.embedding_identity).join(Document).filter(Document.status == 'ready', Document.id != doc_id).all()
        if any(not x[0] or x[0].get('fingerprint') != spec['fingerprint'] for x in existing):
            raise EmbeddingError('Embedding identity changed or is unknown. Ask an admin to rebuild the index, then retry this document.')
        vectors = []
        for offset in range(0, len(passages), 64):
            progress('embedding', len(vectors), len(passages))
            batch, actual = embed([p[0] for p in passages[offset:offset + 64]], spec)
            if len(passages) * actual['dimensions'] > 8_000_000:
                raise EmbeddingError('Document exceeds the 8 million vector component limit. Split it or use smaller embeddings.')
            if vectors and len(vectors[0]) != actual['dimensions']:
                raise EmbeddingError('Embedding dimensions changed between batches. Retry with a stable provider.')
            vectors.extend(batch)
        progress('indexing', len(vectors), len(passages))
        from app.rag.store import get_vector_store
        store = get_vector_store()
        with LOCK:
            current = check()
            if identity()['fingerprint'] != spec['fingerprint']:
                raise EmbeddingError('Embedding settings changed during processing. Retry.')
            rows = [DocChunk(id=uuid.uuid4().hex, document_id=doc_id, ordinal=i, text=text,
                             provenance=location, embedding=vector, embedding_identity=actual)
                    for i, ((text, location), vector) in enumerate(zip(passages, vectors, strict=True))]
            # Unpublished vectors cannot surface: retrieval always validates SQL ready rows.
            store.ensure_collection(actual['dimensions'])
            store.upsert([{'id': r.id, 'dense': r.embedding, 'sparse': sparse_embed(r.text), 'payload': _payload(current, r)} for r in rows])
            current.chunks = rows
            current.n_chunks, current.status, current.error = len(rows), 'ready', None
            current.ingestion = {**current.ingestion, 'stage': 'ready', 'completed': len(rows),
                                 'total': len(rows), 'content_hash': digest, 'parser_version': '2',
                                 'chunker_version': '2', 'embedding': actual}
            db.commit()
    except Exception as exc:
        db.rollback()
        with LOCK:
            current = db.get(Document, doc_id, populate_existing=True)
            if current and current.status == 'processing' and (current.ingestion or {}).get('token') == token:
                current.status = 'interrupted' if isinstance(exc, Interrupted) else 'error'
                current.error = str(exc) if isinstance(exc, (DocumentError, EmbeddingError)) else 'Document processing failed. Check format/provider/index and retry.'
                current.ingestion = {**current.ingestion, 'stage': current.status}
                db.commit()
        logger.warning('Document processing ended: %s', type(exc).__name__)
