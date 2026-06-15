"""Token/cost usage stats (observability).

Two views:
- ``GET /api/usage``          — the signed-in user's own totals (self-service meter).
- ``GET /api/usage/by-user``  — **admin only**, per user × model × month, for finance
  chargebacks. Reads only usage *metadata* (token counts, cost, model, timestamp) — never
  message content — so it stays consistent with the per-user privacy model in AUTH.md.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user, require_admin
from app.database import get_db
from app.models import Conversation, Message, UsageLedger, User

router = APIRouter(prefix="/api/usage", tags=["usage"])


def _parse_date(s: str | None) -> datetime | None:
    """Parse an ISO date/datetime bound, or None. Accepts 'YYYY-MM-DD' or full ISO."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


@router.get("")
def usage_summary(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Aggregate token usage + cost across the user's conversations."""
    rows = (
        db.query(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .filter(Conversation.user_id == user.id, Message.usage.isnot(None))
        .all()
    )
    total_in = total_out = 0
    total_cost = 0.0
    by_model: dict[str, dict] = {}
    for m in rows:
        u = m.usage or {}
        total_in += int(u.get("input", 0) or 0)
        total_out += int(u.get("output", 0) or 0)
        total_cost += float(u.get("cost") or 0.0)
        b = by_model.setdefault(m.model or "?", {"input": 0, "output": 0, "cost": 0.0, "turns": 0})
        b["input"] += int(u.get("input", 0) or 0)
        b["output"] += int(u.get("output", 0) or 0)
        b["cost"] += float(u.get("cost") or 0.0)
        b["turns"] += 1
    return {
        "input_tokens": total_in,
        "output_tokens": total_out,
        "total_tokens": total_in + total_out,
        "cost_usd": round(total_cost, 4),
        "turns": len(rows),
        "by_model": by_model,
    }


@router.get("/by-user")
def usage_by_user(
    start: str | None = Query(None, description="ISO date/datetime lower bound (inclusive)"),
    end: str | None = Query(None, description="ISO date/datetime upper bound (exclusive)"),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Admin accounting view: usage grouped by (month, user, department, model), for
    departmental chargebacks.

    Reads the **durable usage ledger** (``UsageLedger``), not live messages — so usage of
    **deleted users survives** with the identity (username/email/department) that was
    snapshotted at write time. The ledger holds usage **metadata only** (tokens/cost/model),
    never message content. ``start``/``end`` bound ``created_at`` (UTC); months bucket as
    ``YYYY-MM``. A user reassigned to a new department mid-month yields one row per
    department (each billed for the usage incurred while assigned) — intended for chargeback.
    """
    q = db.query(UsageLedger)
    lo, hi = _parse_date(start), _parse_date(end)
    if lo is not None:
        q = q.filter(UsageLedger.created_at >= lo)
    if hi is not None:
        q = q.filter(UsageLedger.created_at < hi)

    # Aggregate in Python (portable across SQLite/Postgres; the ledger is already flat).
    agg: dict[tuple, dict] = {}
    tot_in = tot_out = 0
    tot_cost = 0.0
    for row in q.all():
        month = row.created_at.strftime("%Y-%m") if row.created_at else "?"
        uid = row.user_id or "(unassigned)"
        dept = row.department or "(unassigned)"
        model = row.model or "?"
        cost = float(row.cost_usd or 0.0)
        cell = agg.setdefault(
            (month, uid, dept, model),
            {"username": row.username or "(deleted user)", "email": row.email,
             "input": 0, "output": 0, "cost": 0.0, "turns": 0},
        )
        cell["input"] += row.input_tokens or 0
        cell["output"] += row.output_tokens or 0
        cell["cost"] += cost
        cell["turns"] += 1
        tot_in += row.input_tokens or 0
        tot_out += row.output_tokens or 0
        tot_cost += cost

    rows = [
        {
            "month": month,
            "user_id": uid,
            "username": cell["username"],
            "email": cell["email"],
            "department": dept,
            "model": model,
            "input_tokens": cell["input"],
            "output_tokens": cell["output"],
            "total_tokens": cell["input"] + cell["output"],
            "cost_usd": round(cell["cost"], 4),
            "turns": cell["turns"],
        }
        for (month, uid, dept, model), cell in agg.items()
    ]
    # Stable, report-friendly ordering: newest month first, then department, user, model.
    rows.sort(key=lambda r: (r["department"], r["username"], r["model"]))
    rows.sort(key=lambda r: r["month"], reverse=True)

    return {
        "rows": rows,
        "totals": {
            "input_tokens": tot_in,
            "output_tokens": tot_out,
            "total_tokens": tot_in + tot_out,
            "cost_usd": round(tot_cost, 4),
            "turns": sum(r["turns"] for r in rows),
        },
    }
