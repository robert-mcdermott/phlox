"""Admin CRUD for monthly spend budgets (Settings → Admin → Budgets).

Budgets cap a user's or department's USD spend per UTC calendar month; enforcement lives in
:mod:`app.budgets` and is applied at the chat + gateway model-call choke points. This router
is admin-only and surfaces each budget's **current-month spend** alongside its config so the
admin can see headroom at a glance. See docs/BUDGETS.md.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.deps import require_admin
from app.budgets import current_month_spend
from app.database import get_db
from app.models import Budget, User
from app.schemas import BudgetCreate, BudgetUpdate

router = APIRouter(prefix="/api/admin/budgets", tags=["admin-budgets"])


def _serialize(db: Session, b: Budget) -> dict:
    spent = current_month_spend(db, scope_type=b.scope_type, scope_value=b.scope_value)
    limit = float(b.limit_usd or 0.0)
    # Resolve a friendly label for user-scoped budgets (department scope is self-describing).
    label = b.scope_value
    if b.scope_type == "user":
        u = db.get(User, b.scope_value)
        if u:
            label = u.display_name or u.username
    return {
        "id": b.id,
        "scope_type": b.scope_type,
        "scope_value": b.scope_value,
        "scope_label": label,
        "limit_usd": round(limit, 4),
        "warn_pct": b.warn_pct,
        "is_active": b.is_active,
        "spent_usd": round(spent, 4),
        "remaining_usd": round(max(limit - spent, 0.0), 4),
        "pct": round((spent / limit * 100.0) if limit > 0 else 0.0, 1),
    }


@router.get("")
def list_budgets(db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    """All budgets with their current-month spend. Departments first, then users."""
    rows = db.query(Budget).order_by(Budget.scope_type, Budget.scope_value).all()
    return {"budgets": [_serialize(db, b) for b in rows]}


@router.post("")
def create_budget(
    body: BudgetCreate, db: Session = Depends(get_db), _admin: User = Depends(require_admin)
):
    if body.scope_type not in ("user", "department"):
        raise HTTPException(422, "scope_type must be 'user' or 'department'")
    if not body.scope_value.strip():
        raise HTTPException(422, "scope_value is required")
    if body.limit_usd < 0:
        raise HTTPException(422, "limit_usd must be >= 0")
    if not (0 <= body.warn_pct <= 100):
        raise HTTPException(422, "warn_pct must be between 0 and 100")
    b = Budget(
        scope_type=body.scope_type,
        scope_value=body.scope_value.strip(),
        limit_usd=body.limit_usd,
        warn_pct=body.warn_pct,
        is_active=body.is_active,
    )
    db.add(b)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "A budget for this scope already exists")
    db.refresh(b)
    return _serialize(db, b)


@router.patch("/{budget_id}")
def update_budget(
    budget_id: str,
    body: BudgetUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    b = db.get(Budget, budget_id)
    if not b:
        raise HTTPException(404, "Budget not found")
    if body.limit_usd is not None:
        if body.limit_usd < 0:
            raise HTTPException(422, "limit_usd must be >= 0")
        b.limit_usd = body.limit_usd
    if body.warn_pct is not None:
        if not (0 <= body.warn_pct <= 100):
            raise HTTPException(422, "warn_pct must be between 0 and 100")
        b.warn_pct = body.warn_pct
    if body.is_active is not None:
        b.is_active = body.is_active
    db.commit()
    db.refresh(b)
    return _serialize(db, b)


@router.delete("/{budget_id}", status_code=204)
def delete_budget(
    budget_id: str, db: Session = Depends(get_db), _admin: User = Depends(require_admin)
):
    b = db.get(Budget, budget_id)
    if b:
        db.delete(b)
        db.commit()
