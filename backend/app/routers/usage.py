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
from app.models import UsageLedger, User
from app.model_calls import summarize_rows

router = APIRouter(prefix="/api/usage", tags=["usage"])


def _parse_date(s: str | None) -> datetime | None:
    """Parse an ISO date/datetime bound, or None. Accepts 'YYYY-MM-DD' or full ISO."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _summary(rows):
    summary = summarize_rows(rows)
    return {
        "input_tokens": summary["input"], "output_tokens": summary["output"],
        "total_tokens": summary["total"], "cost_usd": summary["cost"],
        "known_cost_usd": summary["known_cost"],
        "unknown_usage_calls": summary["unknown_usage_calls"],
        "unknown_cost_calls": summary["unknown_cost_calls"], "calls": len(rows),
        "turns": len({r.turn_id or r.message_id or r.id for r in rows}),
    }


@router.get("")
def usage_summary(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Use the same metadata ledger as budgets/chargeback, including paused calls."""
    rows = db.query(UsageLedger).filter_by(user_id=user.id).all()
    grouped = {}
    for row in rows:
        grouped.setdefault(row.model or "?", []).append(row)
    by_model = {}
    for model, entries in grouped.items():
        summary = _summary(entries)
        by_model[model] = {**summary, "input": summary["input_tokens"],
                           "output": summary["output_tokens"], "cost": summary["cost_usd"]}
    return {**_summary(rows), "by_model": by_model}


@router.get("/budget")
def my_budget_status(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """The signed-in user's monthly budget status (applicable user + department budgets).

    Drives the chat warning banner and the pre-send block. Returns empty ``budgets`` when no
    budget covers this user. See :mod:`app.budgets` and docs/BUDGETS.md.
    """
    from app.budgets import budget_status

    return budget_status(db, user)


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

    # Aggregate metadata only; never load conversations or prompt content.
    entries = q.all()
    groups = {}
    for row in entries:
        key = (row.created_at.strftime("%Y-%m"), row.user_id or "(unassigned)",
               row.department or "(unassigned)", row.model or "?")
        groups.setdefault(key, []).append(row)
    rows = [
        {"month": month, "user_id": uid, "department": dept, "model": model,
         "username": cells[0].username or "(deleted user)", "email": cells[0].email,
         **_summary(cells)}
        for (month, uid, dept, model), cells in groups.items()
    ]
    rows.sort(key=lambda r: (r["department"], r["username"], r["model"]))
    rows.sort(key=lambda r: r["month"], reverse=True)
    return {"rows": rows, "totals": _summary(entries)}
