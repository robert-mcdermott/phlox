"""Keep the document index consistent with the configured embedder.

If the embedding model changes (different vector dimension), stored ``DocChunk`` vectors no
longer match what queries produce, so retrieval would fail. ``sync_index`` detects this on
startup (and on demand), re-embeds the stored chunk text with the current embedder, and
rebuilds the vector index. Cheap to call: it only re-embeds when the dimension changed.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models import DocChunk
from app.rag.embed import embed_query, embed_texts
from app.rag.retrieve import reindex_all

logger = logging.getLogger(__name__)

REEMBED_BATCH = 64


def current_embedding_dim() -> int:
    return len(embed_query("dimension probe"))


def reembed_all(db: Session) -> int:
    """Recompute and store embeddings for every chunk with the current embedder."""
    chunks = db.query(DocChunk).all()
    if not chunks:
        return 0
    for i in range(0, len(chunks), REEMBED_BATCH):
        batch = chunks[i : i + REEMBED_BATCH]
        vectors = embed_texts([c.text for c in batch])
        for chunk, vec in zip(batch, vectors, strict=False):
            chunk.embedding = vec
    db.commit()
    return len(chunks)


def sync_index(db: Session) -> dict:
    """Ensure stored embeddings + the vector index match the current embedder.

    Returns {"reembedded": n, "indexed": m, "dim": d}.
    """
    first = db.query(DocChunk).filter(DocChunk.embedding.isnot(None)).first()
    if first is None:
        # No documents yet; nothing to do.
        return {"reembedded": 0, "indexed": 0, "dim": None}

    stored_dim = len(first.embedding)
    try:
        probe_dim = current_embedding_dim()
    except Exception as e:  # noqa: BLE001
        logger.warning("Embedder probe failed (%s); leaving index as-is", e)
        return {"reembedded": 0, "indexed": 0, "dim": stored_dim, "error": str(e)}

    reembedded = 0
    if probe_dim != stored_dim:
        logger.info("Embedder dim changed (%s -> %s); re-embedding documents", stored_dim, probe_dim)
        reembedded = reembed_all(db)

    indexed = reindex_all(db)
    return {"reembedded": reembedded, "indexed": indexed, "dim": probe_dim}
