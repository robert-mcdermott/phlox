"""Hybrid retrieval over the vector store (Qdrant) + reranking.

Flow: dense + sparse query -> RRF fusion (in the store) -> rerank -> top_k. All behind the
``VectorStore``/``Reranker`` seams, so backend or model swaps don't change callers. SQLite
remains the source of truth and can rebuild the index via ``reindex_all``.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session, defer

from app.models import DocChunk, Document
from app.rag.embed import sparse_embed
from app.rag.rerank import get_reranker
from app.rag.store import get_vector_store

logger = logging.getLogger(__name__)

CANDIDATE_MULTIPLIER = 4  # fetch this many * top_k before reranking


def search_chunks(
    db: Session, query: str, top_k: int = 5,
    conversation_id: str | None = None, user_id: str | None = None,
    document_ids: list[str] | None = None, assistant_id: str | None = None,
) -> list[dict]:  # noqa: ARG001
    """Return [{score, text, filename, ordinal, document_id}] for the best matches.

    Restricted to ``user_id``'s documents, and if ``conversation_id`` is
    given, to global documents plus that conversation's scoped documents. ``document_ids``
    can narrow retrieval further for explicit user references. ``assistant_id`` widens the
    scope to that assistant's shared knowledge base — pass only visibility-checked ids.
    """
    from app.rag.identity import embed, identity
    from app.models import Assistant
    from app.runs import LOCK
    from app.rag.embed import _tokenize

    # Scope SQL first. Shared KB visibility is rechecked here, even for stale vector hits.
    def authorized_query():
        personal = Document.user_id == user_id if user_id else Document.id == '__none__'
        shared = Document.id == '__none__'
        if assistant_id:
            assistant = db.get(Assistant, assistant_id, populate_existing=True)
            if assistant and assistant.is_active and (assistant.visibility == 'public' or assistant.created_by == user_id):
                shared = Document.assistant_id == assistant_id
        q = db.query(DocChunk, Document).options(defer(DocChunk.embedding)).join(Document).filter(Document.status == 'ready', personal | shared)
        q = q.filter(Document.conversation_id.is_(None) | (Document.conversation_id == conversation_id))
        if document_ids:
            q = q.filter(Document.id.in_(document_ids))
        return q.populate_existing().order_by(DocChunk.id)

    rows = authorized_query().limit(10001).all()
    if not rows:
        return SearchResults([])
    bounded = len(rows) > 10000
    rows = rows[:10000]
    candidates = {chunk.id: {'score': 0.0, 'text': chunk.text, 'filename': doc.filename,
                            'ordinal': chunk.ordinal, 'document_id': doc.id, 'chunk_id': chunk.id}
                  for chunk, doc in rows}
    notice = 'Search limited to 10,000 accessible chunks. Narrow the selected documents.' if bounded else None
    try:
        configured = identity()
        if any(not c.embedding_identity or c.embedding_identity.get('fingerprint') != configured['fingerprint'] for c, _ in rows):
            raise RuntimeError('Unknown or changed embedding identity')
        dense, actual = embed([query], configured)
        if any(c.embedding_identity.get('dimensions') != actual['dimensions'] for c, _ in rows):
            raise RuntimeError('Incompatible embedding dimensions')
        with LOCK:
            hits = get_vector_store().search(dense[0], sparse_embed(query), limit=max(top_k * CANDIDATE_MULTIPLIER, 10),
                conversation_id=conversation_id, user_id=user_id, document_ids=document_ids, assistant_id=assistant_id)
            # Recheck rows at publication. Never use vector payload text or foreign chunks.
            refreshed = authorized_query().limit(10000).all()
            if any(not c.embedding_identity or c.embedding_identity.get('fingerprint') != actual['fingerprint'] or c.embedding_identity.get('dimensions') != actual['dimensions'] for c, _ in refreshed):
                raise RuntimeError('Index changed during query')
            current = {c.id: {'text': c.text, 'filename': d.filename, 'ordinal': c.ordinal,
                             'document_id': d.id, 'chunk_id': c.id} for c, d in refreshed}
            ranked = [{**current[h['payload']['chunk_id']], 'score': float(h['score'])}
                      for h in hits if h.get('payload', {}).get('chunk_id') in candidates and h['payload']['chunk_id'] in current]
        return SearchResults(get_reranker().rerank(query, ranked, top_k), notice)
    except Exception:
        logger.warning('Document semantic search unavailable; using authorized keyword search')
        terms = set(_tokenize(query))
        with LOCK:
            ranked = [{'score': 0.0, 'text': c.text, 'filename': d.filename, 'ordinal': c.ordinal,
                       'document_id': d.id, 'chunk_id': c.id} for c, d in authorized_query().limit(10000).all()
                      if terms.intersection(_tokenize(c.text))]
        detail = 'Keyword search only: embeddings/index unavailable or incompatible. Results may miss paraphrases; ask an admin to inspect/rebuild the index.'
        return SearchResults(get_reranker().rerank(query, ranked, top_k), detail + (' ' + notice if notice else ''))


class SearchResults(list):
    def __init__(self, rows, notice=None):
        super().__init__(rows)
        self.notice = notice


def reindex_all(db: Session) -> int:
    """Rebuild the vector index from SQLite DocChunks. Returns chunks indexed."""
    rows = (
        db.query(
            DocChunk,
            Document.filename,
            Document.conversation_id,
            Document.user_id,
            Document.assistant_id,
        )
        .join(Document, Document.id == DocChunk.document_id)
        .filter(DocChunk.embedding.isnot(None))
        .all()
    )
    if not rows:
        return 0

    from app.rag.maintenance import state, save
    from app.runs import LOCK
    store = get_vector_store()
    items = [{'id': chunk.id, 'dense': chunk.embedding, 'sparse': sparse_embed(chunk.text),
              'payload': build_chunk_payload(chunk, filename, conv_id, uid, aid)}
             for chunk, filename, conv_id, uid, aid in rows]
    dimensions = {len(item['dense']) for item in items}
    if len(dimensions) != 1:
        raise ValueError('Stored vectors have inconsistent dimensions. Use an explicit embedding rebuild.')
    collection = store.stage(items)
    try:
        with LOCK:
            save(db, {**state(db), 'collection': collection})
            store.activate(collection)
    except BaseException:
        db.rollback()
        store.discard(collection)
        raise
    return len(rows)


def build_chunk_payload(chunk, filename, conversation_id, user_id, assistant_id=None) -> dict:
    """Qdrant payload for a chunk. Omit empty scope keys so IsEmpty filters match."""
    p = {
        "chunk_id": chunk.id,
        "document_id": chunk.document_id,
        "filename": filename,
        "ordinal": chunk.ordinal,
        "text": chunk.text,
    }
    if conversation_id:
        p["conversation_id"] = conversation_id
    if user_id:
        p["user_id"] = user_id
    if assistant_id:
        p["assistant_id"] = assistant_id
    return p
