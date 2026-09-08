"""Explicit, staged index rebuilds; startup never substitutes another embedding model."""
from datetime import datetime, timezone
from sqlalchemy.orm import defer

from app.models import DocChunk, Document, Setting
from app.rag.identity import EmbeddingError, embed, identity
from app.rag.retrieve import build_chunk_payload
from app.rag.embed import sparse_embed
from app.rag.store import get_vector_store
from app.runs import LOCK

INDEX_KEY = 'rag:index'


def state(db):
    row = db.get(Setting, INDEX_KEY, populate_existing=True)
    return dict(row.value or {}) if row else {}


def save(db, values):
    row = db.get(Setting, INDEX_KEY)
    if row is None:
        row = Setting(key=INDEX_KEY)
        db.add(row)
    row.value = {**values, 'updated_at': datetime.now(timezone.utc).isoformat()}
    db.commit()


def status(db):
    saved = state(db)
    try:
        configured = identity()
    except EmbeddingError as exc:
        return {**saved, 'mode': 'keyword', 'notice': str(exc)}
    fingerprint = configured['fingerprint']
    specs = [s[0] for s in db.query(DocChunk.embedding_identity).join(Document).filter(Document.status == 'ready').all()]
    incompatible = any(not s or s.get('fingerprint') != fingerprint for s in specs)
    return {**saved, 'configured': configured, 'rebuild_required': incompatible,
            'mode': 'keyword' if incompatible else ('local-hash' if configured['provider'] == 'hash' else 'hybrid'),
            'notice': 'Embedding identity changed or is unknown. Rebuild the index; keyword search remains available.' if incompatible else None}


def sync_index(db, check=lambda: None):
    """Build vectors and index off to the side, then publish SQL + active collection.

    Called only by an explicit operator/admin request. Caller serializes against ingestion.
    Source snapshots survive; chunk IDs/text/provenance are not replaced by re-embedding.
    """
    rows = db.query(DocChunk).options(defer(DocChunk.embedding)).join(Document).filter(Document.status == 'ready').order_by(DocChunk.id).limit(10001).all()
    if len(rows) > 10000:
        raise EmbeddingError('Rebuild exceeds the 10,000 chunk limit. Reduce the library before rebuilding.')
    spec = identity()
    if not rows:
        return {'indexed': 0, 'reembedded': 0, 'dim': None}
    vectors = []
    def snapshot():
        return list(map(tuple, db.query(DocChunk.id, DocChunk.text, Document.filename,
            Document.user_id, Document.conversation_id, Document.assistant_id).join(Document)
            .filter(Document.status == 'ready').order_by(DocChunk.id).all()))

    initial = snapshot()
    for offset in range(0, len(rows), 64):
        check()
        batch, actual = embed([r.text for r in rows[offset:offset + 64]], spec)
        if len(rows) * actual['dimensions'] > 8_000_000:
            raise EmbeddingError('Rebuild exceeds the 8 million vector component limit. Reduce library size or embedding dimensions.')
        if vectors and len(vectors[0]) != actual['dimensions']:
            raise EmbeddingError('Embedding dimensions changed between batches. Previous index preserved.')
        vectors.extend(batch)
        with LOCK:
            save(db, {**state(db), 'status': 'processing', 'completed': len(vectors), 'total': len(rows)})
    items = []
    for row, vector in zip(rows, vectors, strict=True):
        doc = db.get(Document, row.document_id)
        items.append({'id': row.id, 'dense': vector, 'sparse': sparse_embed(row.text),
                      'payload': build_chunk_payload(row, doc.filename, doc.conversation_id, doc.user_id, doc.assistant_id)})
    store = get_vector_store()
    collection = store.stage(items, check)
    try:
        with LOCK:
            check()
            db.expire_all()
            if snapshot() != initial or identity()['fingerprint'] != spec['fingerprint']:
                raise EmbeddingError('Documents or embedding settings changed during rebuild. Retry; previous index preserved.')
            for row, vector in zip(rows, vectors, strict=True):
                row.embedding, row.embedding_identity = vector, actual
            # Commit vectors and pointer together. activate only swaps an in-process pointer.
            save(db, {**state(db), 'collection': collection, 'identity': actual, 'status': 'ready',
                      'completed': len(rows), 'total': len(rows), 'error': None})
            store.activate(collection)
    except BaseException:
        db.rollback()
        store.discard(collection)
        raise
    return {'indexed': len(rows), 'reembedded': len(rows), 'dim': actual['dimensions']}
