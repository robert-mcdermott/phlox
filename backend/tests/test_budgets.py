"""Tests for monthly spend budgets: status, enforcement, and admin CRUD.

Auth is disabled in the test config, so the TestClient acts as the synthetic dev admin
(``user.id == "local"``, no department). Budgets and ledger rows are written directly and
cleaned up per-test so state doesn't leak into the session-scoped DB.
"""
import uuid
from datetime import datetime, timezone

import pytest

PRICED_MODEL = "budget-test-model"


@pytest.fixture
def clean_budgets():
    """Reset budgets, ledger, and the pricing override before and after each test."""
    def _reset():
        from app import app_config
        from app.database import SessionLocal
        from app.models import AppConfig, Budget, UsageLedger
        s = SessionLocal()
        try:
            s.query(Budget).delete()
            s.query(UsageLedger).delete()
            s.query(AppConfig).delete()
            s.commit()
        finally:
            s.close()
        app_config.invalidate()

    _reset()
    yield
    _reset()


def _price_the_model():
    """Give PRICED_MODEL a configured price so it counts as 'priced' / blockable."""
    from app import app_config
    app_config.set_section("pricing", {PRICED_MODEL: {"input": 1.0, "output": 1.0}})


def _add_spend(user_id, cost, *, department=None, model=PRICED_MODEL, when=None):
    from app.database import SessionLocal
    from app.models import UsageLedger
    s = SessionLocal()
    try:
        s.add(UsageLedger(
            message_id=f"m-{uuid.uuid4().hex}",
            user_id=user_id, department=department, model=model,
            input_tokens=100, output_tokens=100, total_tokens=200, cost_usd=cost,
            created_at=when or datetime.now(timezone.utc).replace(tzinfo=None),
        ))
        s.commit()
    finally:
        s.close()


# -- month window --------------------------------------------------------
def test_month_bounds_wraps_december():
    from app.budgets import month_bounds
    lo, hi = month_bounds(datetime(2025, 12, 15, tzinfo=timezone.utc))
    assert (lo.year, lo.month, lo.day) == (2025, 12, 1)
    assert (hi.year, hi.month) == (2026, 1)


def test_spend_excludes_other_months(clean_budgets):
    from app.budgets import current_month_spend
    from app.database import SessionLocal
    _add_spend("local", 2.0)
    # A row from a clearly prior month must not count toward this month.
    _add_spend("local", 99.0, when=datetime(2000, 1, 1))
    s = SessionLocal()
    try:
        spend = current_month_spend(s, scope_type="user", scope_value="local")
    finally:
        s.close()
    assert spend == 2.0


# -- status + enforcement ------------------------------------------------
def test_budget_status_warns_then_blocks(client, clean_budgets):
    from app.database import SessionLocal
    from app.models import Budget
    _price_the_model()
    s = SessionLocal()
    s.add(Budget(scope_type="user", scope_value="local", limit_usd=5.0, warn_pct=90))
    s.commit()
    s.close()

    # Under the warn threshold: not warning, not blocked.
    _add_spend("local", 1.0)
    st = client.get("/api/usage/budget").json()
    assert st["blocked"] is False and st["warn"] is False
    assert PRICED_MODEL in st["priced_models"]

    # Cross 90% ($4.50 of $5): warn, still not blocked.
    _add_spend("local", 3.6)
    st = client.get("/api/usage/budget").json()
    assert st["warn"] is True and st["blocked"] is False

    # Reach the cap ($5+): blocked.
    _add_spend("local", 1.0)
    st = client.get("/api/usage/budget").json()
    assert st["blocked"] is True


def test_chat_blocked_for_priced_model_over_budget(client, clean_budgets):
    from app.database import SessionLocal
    from app.models import Budget
    _price_the_model()
    s = SessionLocal()
    s.add(Budget(scope_type="user", scope_value="local", limit_usd=1.0, warn_pct=90))
    s.commit()
    s.close()
    _add_spend("local", 2.0)  # over the $1 cap

    r = client.post("/api/chat", json={"message": "hi", "model": PRICED_MODEL})
    assert r.status_code == 402
    assert "budget" in r.text.lower()


def test_chat_allows_unpriced_model_over_budget(client, clean_budgets):
    """An over-budget user can still use a model with no assigned cost."""
    from app.database import SessionLocal
    from app.models import Budget
    _price_the_model()
    s = SessionLocal()
    s.add(Budget(scope_type="user", scope_value="local", limit_usd=1.0, warn_pct=90))
    s.commit()
    s.close()
    _add_spend("local", 2.0)

    # Free model => the budget gate passes (the call later fails at the unreachable
    # provider, but NOT with a 402 budget rejection).
    r = client.post("/api/chat", json={"message": "hi", "model": "free-local-model"})
    assert r.status_code != 402


# -- admin CRUD ----------------------------------------------------------
def test_admin_budget_crud(client, clean_budgets):
    created = client.post("/api/admin/budgets", json={
        "scope_type": "department", "scope_value": "Research", "limit_usd": 5.0,
    }).json()
    assert created["scope_type"] == "department" and created["limit_usd"] == 5.0

    listed = client.get("/api/admin/budgets").json()["budgets"]
    assert any(b["scope_value"] == "Research" for b in listed)

    upd = client.patch(f"/api/admin/budgets/{created['id']}", json={"limit_usd": 10.0}).json()
    assert upd["limit_usd"] == 10.0

    # Duplicate scope is rejected.
    dup = client.post("/api/admin/budgets", json={
        "scope_type": "department", "scope_value": "Research", "limit_usd": 1.0,
    })
    assert dup.status_code == 409

    assert client.delete(f"/api/admin/budgets/{created['id']}").status_code == 204


def test_user_delete_removes_user_budget_keeps_department(clean_budgets):
    """Deleting a user drops their user-scoped budget but leaves department budgets."""
    from app.auth.service import delete_user_data
    from app.database import SessionLocal
    from app.models import Budget, User
    s = SessionLocal()
    try:
        s.add(User(id="u-del", username="to-delete"))
        s.add(Budget(scope_type="user", scope_value="u-del", limit_usd=5.0))
        s.add(Budget(scope_type="department", scope_value="Eng", limit_usd=10.0))
        s.commit()
        delete_user_data(s, "u-del")
        scopes = {(b.scope_type, b.scope_value) for b in s.query(Budget).all()}
        assert ("user", "u-del") not in scopes
        assert ("department", "Eng") in scopes
    finally:
        s.query(User).filter(User.id == "u-del").delete()
        s.commit()
        s.close()


def test_department_spend_attribution(client, clean_budgets):
    """A department budget sums spend snapshotted to that department in the ledger."""
    from app.database import SessionLocal
    from app.models import Budget
    s = SessionLocal()
    s.add(Budget(scope_type="department", scope_value="Eng", limit_usd=10.0))
    s.commit()
    s.close()
    _add_spend("alice", 3.0, department="Eng")
    _add_spend("bob", 4.0, department="Eng")
    _add_spend("carol", 5.0, department="Other")

    listed = client.get("/api/admin/budgets").json()["budgets"]
    eng = next(b for b in listed if b["scope_value"] == "Eng")
    assert eng["spent_usd"] == 7.0
