"""Reranking of retrieval candidates.

``Reranker`` is the seam. The default ``LexicalReranker`` is dependency-free and offline:
it boosts candidates by how fully they cover the query's terms, blended with the fused
retrieval score. To go SOTA, drop in a cross-encoder (e.g. fastembed's reranker) behind
this same interface — ``retrieve.py`` won't change.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.rag.embed import _tokenize


class Reranker(ABC):
    @abstractmethod
    def rerank(self, query: str, candidates: list[dict], top_k: int) -> list[dict]:
        """candidates: [{score, text, ...}] -> reordered, truncated to top_k."""


class LexicalReranker(Reranker):
    def rerank(self, query: str, candidates: list[dict], top_k: int) -> list[dict]:
        q_terms = set(_tokenize(query))
        if not q_terms:
            return candidates[:top_k]
        scored = []
        for c in candidates:
            doc_terms = set(_tokenize(c.get("text", "")))
            coverage = len(q_terms & doc_terms) / len(q_terms)
            # Blend lexical coverage with the fused retrieval score.
            rerank_score = 0.7 * coverage + 0.3 * float(c.get("score", 0.0))
            scored.append({**c, "rerank_score": rerank_score})
        scored.sort(key=lambda x: x["rerank_score"], reverse=True)
        return scored[:top_k]


_reranker: Reranker | None = None


def get_reranker() -> Reranker:
    global _reranker
    if _reranker is None:
        _reranker = LexicalReranker()
    return _reranker
