"""Hybrid retrieval over the vector store (Qdrant) + reranking.

Flow: dense + sparse query -> RRF fusion (in the store) -> rerank -> top_k. All behind the
``VectorStore``/``Reranker`` seams, so backend or model swaps don't change callers. SQLite
remains the source of truth and can rebuild the index via ``reindex_all``.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models import DocChunk, Document
from app.rag.embed import embed_query, sparse_embed
from app.rag.rerank import get_reranker
from app.rag.store import get_vector_store

logger = logging.getLogger(__name__)

CANDIDATE_MULTIPLIER = 4  # fetch this many * top_k before reranking


def search_chunks(
    db: Session, query: str, top_k: int = 5,
    conversation_id: str | None = None, user_id: str | None = None,
) -> list[dict]:  # noqa: ARG001
    """Return [{score, text, filename, ordinal, document_id}] for the best matches.

    Restricted to ``user_id``'s documents (+ legacy unowned), and if ``conversation_id`` is
    given, to global documents plus that conversation's scoped documents.
    """
    dense = embed_query(query)
    sparse = sparse_embed(query)
    candidate_n = max(top_k * CANDIDATE_MULTIPLIER, 10)
    try:
        hits = get_vector_store().search(
            dense, sparse, limit=candidate_n,
            conversation_id=conversation_id, user_id=user_id,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Vector search failed: %s", e)
        return []

    candidates = []
    for h in hits:
        p = h["payload"]
        candidates.append(
            {
                "score": float(h["score"]),
                "text": p.get("text", ""),
                "filename": p.get("filename", ""),
                "ordinal": p.get("ordinal", 0),
                "document_id": p.get("document_id", ""),
            }
        )

    return get_reranker().rerank(query, candidates, top_k)


def reindex_all(db: Session) -> int:
    """Rebuild the vector index from SQLite DocChunks. Returns chunks indexed."""
    rows = (
        db.query(DocChunk, Document.filename, Document.conversation_id, Document.user_id)
        .join(Document, Document.id == DocChunk.document_id)
        .filter(DocChunk.embedding.isnot(None))
        .all()
    )
    if not rows:
        return 0

    store = get_vector_store()
    store.recreate_collection(len(rows[0][0].embedding))
    store.upsert(
        [
            {
                "id": chunk.id,
                "dense": chunk.embedding,
                "sparse": sparse_embed(chunk.text),
                "payload": build_chunk_payload(chunk, filename, conv_id, uid),
            }
            for chunk, filename, conv_id, uid in rows
        ]
    )
    return len(rows)


def build_chunk_payload(chunk, filename, conversation_id, user_id) -> dict:
    """Qdrant payload for a chunk. Omit empty scope keys so IsEmpty filters match."""
    p = {
        "document_id": chunk.document_id,
        "filename": filename,
        "ordinal": chunk.ordinal,
        "text": chunk.text,
    }
    if conversation_id:
        p["conversation_id"] = conversation_id
    if user_id:
        p["user_id"] = user_id
    return p
