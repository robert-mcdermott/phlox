"""Vector store abstraction + Qdrant implementation (hybrid dense + sparse).

``VectorStore`` is the seam: ingest/retrieve only depend on this interface, so the backend
can change without touching RAG logic. The default ``QdrantVectorStore`` runs **embedded**
(on-disk, no server) and upgrades to a Qdrant **server** by setting ``url`` instead of
``path`` in ``config.yml`` — identical query code.

Hybrid retrieval: each chunk is indexed with a named **dense** vector (semantic) and a
named **sparse** vector (lexical/BM25-ish). ``search`` queries both and fuses the results
with Reciprocal Rank Fusion (RRF) in Python — robust across embedded and server modes.
SQLite (``DocChunk``) stays the source of truth; the index rebuilds via ``reindex_all``.
"""
from __future__ import annotations

import logging
import threading
import uuid
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)

DENSE = "dense"
SPARSE = "sparse"
RRF_K = 60  # RRF dampening constant


class VectorStore(ABC):
    @abstractmethod
    def ensure_collection(self, dim: int) -> None: ...

    @abstractmethod
    def recreate_collection(self, dim: int) -> None:
        """Drop and recreate the collection at ``dim`` (atomic clean rebuild)."""

    @abstractmethod
    def upsert(self, items: list[dict[str, Any]]) -> None:
        """items: [{id, dense, sparse, payload}]. ``sparse`` is {indices, values}."""

    @abstractmethod
    def search(
        self, dense: list[float], sparse: dict, limit: int,
        conversation_id: str | None = None, user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Hybrid search; returns up to ``limit`` fused [{score, payload}].

        Restricted to the given ``user_id`` (plus legacy unowned) and, if
        ``conversation_id`` is given, to global docs plus that conversation's scoped docs.
        """

    @abstractmethod
    def delete_by_document(self, document_id: str) -> None: ...

    @abstractmethod
    def reset(self) -> None: ...


def _point_id(chunk_id: str) -> str:
    try:
        return str(uuid.UUID(hex=chunk_id))
    except ValueError:
        return str(uuid.uuid5(uuid.NAMESPACE_OID, chunk_id))


def _rrf_fuse(result_lists: list[list], limit: int) -> list[dict]:
    """Reciprocal Rank Fusion over multiple ranked lists of scored points."""
    agg: dict[Any, dict] = {}
    for results in result_lists:
        for rank, point in enumerate(results):
            entry = agg.setdefault(point.id, {"score": 0.0, "payload": point.payload or {}})
            entry["score"] += 1.0 / (RRF_K + rank + 1)
    fused = sorted(agg.values(), key=lambda x: x["score"], reverse=True)
    return fused[:limit]


class QdrantVectorStore(VectorStore):
    def __init__(self, config: dict[str, Any]):
        from qdrant_client import QdrantClient

        self.collection = config.get("collection", "doc_chunks")
        self._lock = threading.Lock()  # embedded Qdrant's local client isn't thread-safe
        if config.get("url"):
            self._client = QdrantClient(url=config["url"], api_key=config.get("api_key"))
            self._mode = f"server:{config['url']}"
        else:
            self._client = QdrantClient(path=config["path"])
            self._mode = f"embedded:{config['path']}"
        self._dim: int | None = None
        logger.info("Qdrant vector store (%s), collection=%s", self._mode, self.collection)

    def ensure_collection(self, dim: int) -> None:
        from qdrant_client.models import Distance, SparseVectorParams, VectorParams

        with self._lock:
            recreate = False
            if self._client.collection_exists(self.collection):
                info = self._client.get_collection(self.collection)
                vectors = info.config.params.vectors
                # Recreate if missing the named dense vector or dim changed.
                current = vectors.get(DENSE).size if isinstance(vectors, dict) and DENSE in vectors else None
                if current != dim:
                    logger.warning("Collection schema/dim mismatch; recreating")
                    self._client.delete_collection(self.collection)
                    recreate = True
            else:
                recreate = True
            if recreate:
                self._client.create_collection(
                    collection_name=self.collection,
                    vectors_config={DENSE: VectorParams(size=dim, distance=Distance.COSINE)},
                    sparse_vectors_config={SPARSE: SparseVectorParams()},
                )
            self._dim = dim

    def recreate_collection(self, dim: int) -> None:
        from qdrant_client.models import Distance, SparseVectorParams, VectorParams

        with self._lock:
            if self._client.collection_exists(self.collection):
                self._client.delete_collection(self.collection)
            self._client.create_collection(
                collection_name=self.collection,
                vectors_config={DENSE: VectorParams(size=dim, distance=Distance.COSINE)},
                sparse_vectors_config={SPARSE: SparseVectorParams()},
            )
            self._dim = dim

    def upsert(self, items: list[dict[str, Any]]) -> None:
        from qdrant_client.models import PointStruct, SparseVector

        if not items:
            return
        if self._dim is None:
            self.ensure_collection(len(items[0]["dense"]))
        points = []
        for it in items:
            sp = it.get("sparse") or {"indices": [], "values": []}
            points.append(
                PointStruct(
                    id=_point_id(it["id"]),
                    vector={
                        DENSE: it["dense"],
                        SPARSE: SparseVector(indices=sp["indices"], values=sp["values"]),
                    },
                    payload=it["payload"],
                )
            )
        with self._lock:
            self._client.upsert(collection_name=self.collection, points=points)

    def _scope_filter(self, user_id: str | None, conversation_id: str | None):
        """Restrict to (the user's docs + legacy unowned) AND (global + this conversation)."""
        from qdrant_client.models import FieldCondition, Filter, IsEmptyCondition, MatchValue, PayloadField

        must = []
        if user_id:
            # Strict: only the owner's chunks (no legacy/unowned allowance) — privacy.
            must.append(FieldCondition(key="user_id", match=MatchValue(value=user_id)))
        if conversation_id:
            must.append(
                Filter(
                    should=[
                        IsEmptyCondition(is_empty=PayloadField(key="conversation_id")),
                        FieldCondition(key="conversation_id", match=MatchValue(value=conversation_id)),
                    ]
                )
            )
        return Filter(must=must) if must else None

    def search(
        self, dense: list[float], sparse: dict, limit: int,
        conversation_id: str | None = None, user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        from qdrant_client.models import SparseVector

        qfilter = self._scope_filter(user_id, conversation_id)
        with self._lock:
            if not self._client.collection_exists(self.collection):
                return []
            dense_hits = self._client.query_points(
                collection_name=self.collection, query=dense, using=DENSE,
                limit=limit, with_payload=True, query_filter=qfilter,
            ).points
            sparse_hits = []
            if sparse.get("indices"):
                sparse_hits = self._client.query_points(
                    collection_name=self.collection,
                    query=SparseVector(indices=sparse["indices"], values=sparse["values"]),
                    using=SPARSE, limit=limit, with_payload=True, query_filter=qfilter,
                ).points
        return _rrf_fuse([dense_hits, sparse_hits], limit)

    def delete_by_document(self, document_id: str) -> None:
        from qdrant_client.models import FieldCondition, Filter, FilterSelector, MatchValue

        with self._lock:
            if not self._client.collection_exists(self.collection):
                return
            self._client.delete(
                collection_name=self.collection,
                points_selector=FilterSelector(
                    filter=Filter(
                        must=[FieldCondition(key="document_id", match=MatchValue(value=document_id))]
                    )
                ),
            )

    def reset(self) -> None:
        with self._lock:
            if self._client.collection_exists(self.collection):
                self._client.delete_collection(self.collection)
            self._dim = None


_store: VectorStore | None = None


def get_vector_store() -> VectorStore:
    """Process-wide vector store. Swap the implementation here to change backends."""
    global _store
    if _store is None:
        from app.config import get_vector_store_config

        cfg = get_vector_store_config()
        if cfg.get("provider", "qdrant") != "qdrant":
            raise ValueError(f"Unsupported vector_store provider: {cfg.get('provider')}")
        _store = QdrantVectorStore(cfg)
    return _store
