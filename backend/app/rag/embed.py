"""Embeddings with a graceful offline fallback.

If an embeddings provider is configured (``config.yml`` ``embeddings:``) it is used.
Otherwise a deterministic local hashing embedder keeps RAG functional offline — lower
quality, but it never blocks the feature.
"""
from __future__ import annotations

import hashlib
import logging
import math

logger = logging.getLogger(__name__)

FALLBACK_DIM = 512


def _hash_embed(text: str, dim: int = FALLBACK_DIM) -> list[float]:
    """Cheap bag-of-words hashing embedding (deterministic, normalized)."""
    vec = [0.0] * dim
    for tok in text.lower().split():
        h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)  # noqa: S324
        vec[h % dim] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts (dense), using the configured provider or the fallback."""
    from app.providers.registry import build_embedder

    provider, model = build_embedder()
    if provider is not None:
        try:
            return provider.embed(texts, model=model)
        except Exception as e:  # noqa: BLE001
            logger.warning("Embedding provider failed (%s); using local fallback", e)
    return [_hash_embed(t) for t in texts]


def embed_query(text: str) -> list[float]:
    return embed_texts([text])[0]


# --- Sparse (lexical) embeddings for hybrid search ------------------------
# A dependency-free, offline BM25-ish sparse vector: hashed-token term frequency with
# sublinear scaling and L2 normalization. Captures exact-term matches that dense vectors
# miss. (Future: corpus IDF weighting or a fastembed BM25 model — same interface.)
SPARSE_DIM = 1 << 20


def _tokenize(text: str) -> list[str]:
    out: list[str] = []
    for raw in text.lower().split():
        tok = "".join(ch for ch in raw if ch.isalnum())
        if len(tok) >= 2:
            out.append(tok)
    return out


def _tok_index(tok: str) -> int:
    return int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16) % SPARSE_DIM  # noqa: S324


def sparse_embed(text: str) -> dict[str, list]:
    """Return a sparse vector as {"indices": [...], "values": [...]}."""
    counts: dict[int, int] = {}
    for tok in _tokenize(text):
        idx = _tok_index(tok)
        counts[idx] = counts.get(idx, 0) + 1
    if not counts:
        return {"indices": [], "values": []}
    weights = {i: 1.0 + math.log(c) for i, c in counts.items()}
    norm = math.sqrt(sum(w * w for w in weights.values())) or 1.0
    indices = list(weights.keys())
    values = [weights[i] / norm for i in indices]
    return {"indices": indices, "values": values}
