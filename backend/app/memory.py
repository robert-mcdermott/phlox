"""Cross-conversation memory: durable facts retrieved into the system prompt.

Memories are small in number, so similarity is a simple cosine in Python over the stored
embedding (reusing the RAG embedder). ``retrieve_memories`` is called per turn to surface
relevant memories; ``save_memory`` is exposed to the agent as a tool and to the user via
the Memory settings tab.
"""
from __future__ import annotations

import logging

import numpy as np
from sqlalchemy.orm import Session

from app.models import Memory
from app.rag.embed import embed_query, embed_texts

logger = logging.getLogger(__name__)

RETRIEVE_TOP_K = 5
MIN_SCORE = 0.20


def save_memory(
    db: Session, content: str, kind: str = "fact",
    conversation_id: str | None = None, user_id: str | None = None,
) -> Memory:
    content = content.strip()
    # De-dupe exact repeats for the same user.
    existing = (
        db.query(Memory).filter(Memory.content == content, Memory.user_id == user_id).first()
    )
    if existing:
        return existing
    try:
        embedding = embed_texts([content])[0]
    except Exception as e:  # noqa: BLE001
        logger.warning("Memory embedding failed: %s", e)
        embedding = None
    mem = Memory(
        content=content, kind=kind, source_conversation_id=conversation_id,
        embedding=embedding, user_id=user_id,
    )
    db.add(mem)
    db.commit()
    db.refresh(mem)
    return mem


def list_memories(db: Session, user_id: str | None = None) -> list[Memory]:
    return (
        db.query(Memory).filter(Memory.user_id == user_id).order_by(Memory.created_at.desc()).all()
    )


def delete_memory(db: Session, memory_id: str, user_id: str | None = None) -> bool:
    mem = db.get(Memory, memory_id)
    if not mem or (user_id is not None and mem.user_id != user_id):
        return False
    db.delete(mem)
    db.commit()
    return True


def retrieve_memories(
    db: Session, query: str, top_k: int = RETRIEVE_TOP_K, user_id: str | None = None
) -> list[Memory]:
    rows = (
        db.query(Memory)
        .filter(Memory.embedding.isnot(None), Memory.user_id == user_id)
        .all()
    )
    if not rows:
        return []
    qv = np.asarray(embed_query(query), dtype=np.float32)
    qn = np.linalg.norm(qv) or 1.0
    scored: list[tuple[float, Memory]] = []
    for m in rows:
        cv = np.asarray(m.embedding, dtype=np.float32)
        if cv.shape != qv.shape:
            continue
        score = float(np.dot(qv, cv) / (qn * (np.linalg.norm(cv) or 1.0)))
        if score >= MIN_SCORE:
            scored.append((score, m))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [m for _s, m in scored[:top_k]]


def memory_preamble(db: Session, query: str, user_id: str | None = None) -> str:
    """Return a system-prompt fragment of relevant memories, or '' if none."""
    mems = retrieve_memories(db, query, user_id=user_id)
    if not mems:
        return ""
    lines = "\n".join(f"- ({m.kind}) {m.content}" for m in mems)
    return f"\n\nRelevant long-term memory about the user (use if helpful):\n{lines}"
