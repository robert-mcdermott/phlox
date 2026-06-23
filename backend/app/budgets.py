"""Monthly spend budgets: current-month spend, status, and enforcement.

Budgets (``models.Budget``) cap a user's or department's USD spend per UTC calendar month.
Spend is computed live from the durable ``UsageLedger`` (the same metadata that drives
chargeback), so there is no counter to reset — the month window simply rolls forward.

Enforcement is **most-restrictive-wins**: a user may be covered by both their own user
budget and their department budget; a turn is blocked if *either* is at/over its limit. Only
**priced** models (those with an ``observability.pricing`` entry) count toward spend and only
priced models are blocked — free/local models stay usable.

Because token cost is only known after a turn completes, enforcement blocks the *next* turn
once you are at/over budget rather than cutting off mid-turn (the turn that crosses the line
is allowed to finish). See docs/BUDGETS.md.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Budget, UsageLedger, User


def month_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    """Return ``[start_of_month, start_of_next_month)`` in UTC for ``now`` (default: now)."""
    now = now or datetime.now(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    # First day of next month (December wraps to January of the next year).
    if start.month == 12:
        nxt = start.replace(year=start.year + 1, month=1)
    else:
        nxt = start.replace(month=start.month + 1)
    return start, nxt


def priced_models() -> set[str]:
    """Model ids that have a configured price (and so count toward spend / can be blocked)."""
    from app.config import get_observability_config

    return set((get_observability_config().get("pricing") or {}).keys())


def model_is_priced(model: str | None) -> bool:
    return bool(model) and model in priced_models()


def current_month_spend(
    db: Session, *, scope_type: str, scope_value: str, now: datetime | None = None
) -> float:
    """Sum ``cost_usd`` from the ledger for one scope over the current UTC month.

    ``user`` scope matches ledger rows by the snapshotted ``user_id``; ``department`` scope
    matches by the snapshotted ``department`` name (so a user reassigned mid-month bills the
    department they were in at the time of each turn — consistent with the chargeback view).
    """
    lo, hi = month_bounds(now)
    q = db.query(func.coalesce(func.sum(UsageLedger.cost_usd), 0.0)).filter(
        UsageLedger.created_at >= lo, UsageLedger.created_at < hi
    )
    if scope_type == "user":
        q = q.filter(UsageLedger.user_id == scope_value)
    else:
        q = q.filter(UsageLedger.department == scope_value)
    return float(q.scalar() or 0.0)


def applicable_budgets(db: Session, user: User) -> list[Budget]:
    """Active budgets that cover ``user``: their own user budget + their department budget."""
    out: list[Budget] = []
    ub = (
        db.query(Budget)
        .filter(Budget.is_active.is_(True), Budget.scope_type == "user",
                Budget.scope_value == user.id)
        .first()
    )
    if ub:
        out.append(ub)
    if user.department:
        db_ = (
            db.query(Budget)
            .filter(Budget.is_active.is_(True), Budget.scope_type == "department",
                    Budget.scope_value == user.department)
            .first()
        )
        if db_:
            out.append(db_)
    return out


def _budget_cell(db: Session, b: Budget, now: datetime | None = None) -> dict:
    spent = current_month_spend(db, scope_type=b.scope_type, scope_value=b.scope_value, now=now)
    limit = float(b.limit_usd or 0.0)
    pct = (spent / limit * 100.0) if limit > 0 else 0.0
    over = limit > 0 and spent >= limit
    warn = bool(b.warn_pct) and limit > 0 and pct >= b.warn_pct and not over
    return {
        "id": b.id,
        "scope_type": b.scope_type,
        "scope_value": b.scope_value,
        "limit_usd": round(limit, 4),
        "spent_usd": round(spent, 4),
        "remaining_usd": round(max(limit - spent, 0.0), 4),
        "pct": round(pct, 1),
        "warn_pct": b.warn_pct,
        "over": over,
        "warn": warn,
    }


def budget_status(db: Session, user: User, now: datetime | None = None) -> dict:
    """The signed-in user's budget status, for the UI banner and pre-send checks.

    ``blocked`` is true if any applicable budget is at/over its limit (priced models will be
    refused). ``warn`` is true if any applicable budget has crossed its warn threshold but
    none are over. ``worst`` is the closest-to-limit applicable budget. ``priced_models``
    lets the client mark/disable exactly the models that would be refused.
    """
    cells = [_budget_cell(db, b, now=now) for b in applicable_budgets(db, user)]
    blocked = any(c["over"] for c in cells)
    warn = (not blocked) and any(c["warn"] for c in cells)
    worst = max(cells, key=lambda c: c["pct"], default=None)
    return {
        "budgets": cells,
        "blocked": blocked,
        "warn": warn,
        "worst": worst,
        "priced_models": sorted(priced_models()),
    }


def enforce_budget(db: Session, user: User, model: str | None) -> None:
    """Raise HTTP 402 if ``user`` is over budget and ``model`` is priced. No-op otherwise.

    Called at both model-call choke points (interactive chat + the gateway) so API-key
    traffic is gated identically to the UI. Unpriced models are always allowed.
    """
    if not model_is_priced(model):
        return
    status = budget_status(db, user)
    if not status["blocked"]:
        return
    worst = status["worst"] or {}
    scope = "department" if worst.get("scope_type") == "department" else "your"
    raise HTTPException(
        402,
        detail=(
            f"Monthly budget exceeded for {scope} budget "
            f"(${worst.get('spent_usd', 0):.2f} of ${worst.get('limit_usd', 0):.2f}). "
            f"Access to priced models is paused until the budget resets next month. "
            f"Models without an assigned cost remain available."
        ),
    )
