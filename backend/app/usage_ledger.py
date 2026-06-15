"""The durable usage ledger: write + backfill helpers.

The ledger (``models.UsageLedger``) is an append-only, FK-free record of per-turn token
usage and cost, with the billable user's identity **snapshotted** at write time. It outlives
user/conversation deletion so departmental chargebacks survive a user leaving mid-month.
See the ``UsageLedger`` docstring and docs/AUTH.md for the privacy rationale (metadata only).

Two entry points:
- ``record_usage`` — called by the harness when a turn's usage is finalized.
- ``backfill_usage_ledger`` — one-time/idempotent import of pre-existing ``Message.usage``
  rows, run at startup so historical cost isn't lost when this feature ships.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models import Conversation, Message, UsageLedger, User

logger = logging.getLogger(__name__)


def _identity_snapshot(db: Session, user_id: str | None) -> dict:
    """Freeze the billable identity for a ledger row. Falls back gracefully if the user
    can't be resolved (pre-auth data, already-deleted account during backfill)."""
    user = db.get(User, user_id) if user_id else None
    if user is None:
        return {"user_id": user_id, "username": None, "email": None, "department": None}
    return {
        "user_id": user.id,
        "username": user.username,
        "email": user.email,
        "department": user.department,
    }


def record_usage(
    db: Session,
    *,
    message_id: str,
    conversation_id: str,
    user_id: str | None,
    model: str | None,
    usage: dict,
) -> None:
    """Append one ledger row for a finalized assistant turn. Best-effort: a ledger failure
    must never break the chat turn, so callers should not let exceptions propagate.

    Idempotent on ``message_id`` (a regenerate/resume re-finalizing the same message won't
    double-count)."""
    if db.query(UsageLedger).filter(UsageLedger.message_id == message_id).first():
        return
    ident = _identity_snapshot(db, user_id)
    inp = int(usage.get("input", 0) or 0)
    out = int(usage.get("output", 0) or 0)
    row = UsageLedger(
        message_id=message_id,
        conversation_id=conversation_id,
        model=model,
        input_tokens=inp,
        output_tokens=out,
        total_tokens=int(usage.get("total", inp + out) or (inp + out)),
        cost_usd=usage.get("cost"),
        **ident,
    )
    db.add(row)
    db.commit()


def backfill_usage_ledger(db: Session) -> int:
    """Create ledger rows for any historical ``Message.usage`` that predates the ledger.

    Idempotent (skips messages already in the ledger via ``message_id``), so it's safe to
    run on every startup. Returns the number of rows created."""
    existing = {
        mid
        for (mid,) in db.query(UsageLedger.message_id).filter(UsageLedger.message_id.isnot(None))
    }
    # Owner lookup per conversation, cached to avoid a query per message.
    owner_of: dict[str, str | None] = {}
    created = 0
    for msg in db.query(Message).filter(Message.usage.isnot(None)):
        if msg.id in existing:
            continue
        u = msg.usage or {}
        if not u.get("total"):
            continue
        if msg.conversation_id not in owner_of:
            conv = db.get(Conversation, msg.conversation_id)
            owner_of[msg.conversation_id] = conv.user_id if conv else None
        ident = _identity_snapshot(db, owner_of[msg.conversation_id])
        inp = int(u.get("input", 0) or 0)
        out = int(u.get("output", 0) or 0)
        db.add(
            UsageLedger(
                message_id=msg.id,
                conversation_id=msg.conversation_id,
                model=msg.model,
                input_tokens=inp,
                output_tokens=out,
                total_tokens=int(u.get("total", inp + out) or (inp + out)),
                cost_usd=u.get("cost"),
                created_at=msg.created_at,
                **ident,
            )
        )
        created += 1
    if created:
        db.commit()
        logger.info("Usage ledger backfill: created %d historical rows", created)
    return created
