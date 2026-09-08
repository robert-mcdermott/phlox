"""Offline contracts for approval claims, counters, policy changes and recovery."""
import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.agent.harness import AgentSession
from app.agent.permissions import PermissionGate
from app.agent.registry import ToolRegistry
from app.agent.tools.base import Tool, ToolResult
from app.models import Assistant, Conversation, Message, PendingApproval, ToolPref, UsageLedger
from app.providers.base import StreamDelta, ToolCall


def frames(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


@pytest.fixture
def approval(db, monkeypatch):
    from app.routers import chat

    executed = []
    registry = ToolRegistry()

    class Action(Tool):
        name = "wave2_action"
        default_permission = "ask"

        def run(self, ctx, **kwargs):
            executed.append(kwargs["number"])
            return ToolResult(content=f"action {kwargs['number']}")

    class Provider:
        model = "wave2-model"
        supports_tools = True

        def stream(self, messages, tools, params):
            count = sum(1 for m in messages if m.get("tool_calls"))
            yield StreamDelta(type="usage", usage={"input": 7, "output": 3, "total": 10})
            if count < 2:
                yield StreamDelta(type="text", text=f"Plan {count + 1}")
                yield StreamDelta(type="tool_calls", tool_calls=[
                    ToolCall(f"call-{count + 1}", "wave2_action", {"number": count + 1})
                ])
            else:
                yield StreamDelta(type="text", text="Finished both actions.")
                yield StreamDelta(type="done")

    registry.register(Action())
    monkeypatch.setattr(chat, "REGISTRY", registry)
    monkeypatch.setattr(chat, "build_provider", lambda *a: Provider())
    conv = Conversation(title="approval tests", user_id="local")
    db.add(conv)
    db.commit()
    session = AgentSession(db, conv, Provider(), registry, PermissionGate(db, registry),
                           {"max_tool_rounds": 3}, "test", "wave2-model")
    list(session.run([{"role": "system", "content": "PRIVATE SYSTEM CONTEXT"},
                      {"role": "user", "content": "two actions"}]))
    pending = db.query(PendingApproval).filter_by(conversation_id=conv.id).one()
    yield {"pending": pending, "conv": conv, "executed": executed, "provider": Provider,
           "registry": registry, "session": session}
    db.rollback()
    db.query(ToolPref).filter_by(name="wave2_action").delete()
    db.query(UsageLedger).filter_by(conversation_id=conv.id).delete()
    db.query(PendingApproval).filter_by(conversation_id=conv.id).delete()
    db.query(Message).filter_by(conversation_id=conv.id).delete()
    db.delete(conv)
    db.commit()


def submit(client, pending, decisions=None):
    return client.post("/api/chat/approve", json={"pending_id": pending.id, "decisions":
        decisions if decisions is not None else {
            c["id"]: "allow" for c in pending.state["pending_calls"]
        }})


def test_two_pauses_keep_usage_rounds_without_duplicate_ledger_entries(client, db, approval):
    p = approval["pending"]
    assert p.state["rounds_used"] == 1 and p.state["turn_usage"]["total"] == 10
    response = submit(client, p)
    assert response.status_code == 200
    second_id = next(e["pending_id"] for e in frames(response) if e["type"] == "paused")
    db.expire_all()
    second = db.get(PendingApproval, second_id)
    assert second.state["rounds_used"] == 2 and second.state["turn_usage"]["total"] == 20
    response = submit(client, second)
    assert next(e for e in frames(response) if e["type"] == "done")["outcome"] == "completed"
    db.expire_all()
    messages = db.query(Message).filter_by(conversation_id=approval["conv"].id).all()
    assert len(messages) == 1 and messages[0].usage["total"] == 30
    ledger = db.query(UsageLedger).filter_by(conversation_id=approval["conv"].id).all()
    assert len(ledger) == 3 and sum(row.total_tokens for row in ledger) == 30
    assert len({row.turn_id for row in ledger}) == 1
    assert approval["executed"] == [1, 2]
    assert p.status == "paused" and second.status == "completed"
    assert client.get(f'/api/chat/approvals/{approval["conv"].id}').json() == []


def test_round_budget_cannot_be_reset_by_approval(client, db, approval):
    p = approval["pending"]
    state = copy.deepcopy(p.state)
    state["params"]["max_tool_rounds"] = 1
    p.state = state
    db.commit()
    response = submit(client, p)
    assert next(e for e in frames(response) if e["type"] == "done")["outcome"] == "limit_reached"
    assert approval["executed"] == [1]  # finish the already-budgeted round, no next model call
    msg = db.query(Message).filter_by(conversation_id=approval["conv"].id).one()
    assert msg.usage["total"] == 10


def test_lowered_current_limit_blocks_excess_pending_actions(client, db, approval, monkeypatch):
    from app.routers import chat

    p = approval["pending"]
    state = copy.deepcopy(p.state)
    state["rounds_used"] = 2
    p.state = state
    db.commit()
    monkeypatch.setattr(chat, "get_settings", lambda *a: {"max_tool_rounds": 1})
    response = submit(client, p)
    assert '"outcome": "limit_reached"' in response.text
    assert approval["executed"] == []


@pytest.mark.parametrize("decisions", [{}, {"extra": "allow"}, {"call-1": "yes"},
                                        {"call-1": "allow", "extra": "deny"}])
def test_decisions_must_be_exact_and_valid(client, db, approval, decisions):
    assert submit(client, approval["pending"], decisions).status_code == 422
    db.refresh(approval["pending"])
    assert approval["pending"].status == "pending" and not approval["executed"]


def test_duplicate_concurrent_submissions_dispatch_once(client, db, approval, monkeypatch):
    from app.routers import chat

    barrier = threading.Barrier(2)

    def build(*args):
        barrier.wait(timeout=5)
        return approval["provider"]()

    monkeypatch.setattr(chat, "build_provider", build)
    # Materialize the request before threads start: never share the fixture's DB session.
    payload = {"pending_id": approval["pending"].id, "decisions": {"call-1": "allow"}}
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: client.post("/api/chat/approve", json=payload), range(2)))
    assert sorted(r.status_code for r in responses) == [200, 409]
    assert approval["executed"] == [1]


@pytest.mark.parametrize("enabled,permission", [(False, "ask"), (True, "deny")])
def test_current_tool_policy_wins_over_allow_decision(client, db, approval, enabled, permission):
    db.add(ToolPref(name="wave2_action", enabled=enabled, permission=permission))
    db.commit()
    response = submit(client, approval["pending"])
    assert response.status_code == 200 and "not enabled" in response.text
    assert not approval["executed"]


def test_explicit_deny_is_honored(client, approval):
    response = submit(client, approval["pending"], {"call-1": "deny"})
    assert response.status_code == 200 and "was denied" in response.text
    assert not approval["executed"]


@pytest.mark.parametrize("kind", ["expired", "legacy"])
def test_old_approvals_are_visible_but_not_replayed(client, db, approval, kind):
    p = approval["pending"]
    if kind == "expired":
        p.created_at = datetime.now(timezone.utc) - timedelta(days=2)
    else:
        state = dict(p.state)
        state.pop("version")
        p.state = state
    db.commit()
    listing = client.get(f'/api/chat/approvals/{approval["conv"].id}').json()
    assert listing[0]["status"] == kind
    assert submit(client, p).status_code == 409 and not approval["executed"]
    assert client.delete(f"/api/chat/approvals/{p.id}").status_code == 200


def test_recovery_exposes_only_owner_projection_and_blocks_stale_history(client, db, approval):
    cid, pid = approval["conv"].id, approval["pending"].id
    response = client.get(f"/api/chat/approvals/{cid}")
    assert response.status_code == 200
    assert "PRIVATE SYSTEM CONTEXT" not in response.text
    snapshot = response.json()[0]
    assert snapshot["content"] == "Plan 1" and snapshot["calls"][0]["id"] == "call-1"
    assert "messages" not in snapshot and "params" not in snapshot
    assert client.post("/api/chat", json={"conversation_id": cid, "message": "new"}).status_code == 409
    msg = Message(conversation_id=cid, role="user", content="older message")
    db.add(msg)
    db.commit()
    assert client.delete(f"/api/conversations/{cid}/messages/{msg.id}").status_code == 409
    assert client.delete(f"/api/chat/approvals/{pid}").status_code == 200
    assert submit(client, approval["pending"]).status_code == 409


def test_claim_survives_new_request_without_replay_or_dismissal(client, db, approval):
    from app.approvals import claim

    claim(db, approval["pending"].id)
    listing = client.get(f'/api/chat/approvals/{approval["conv"].id}').json()
    assert listing[0]["status"] == "claimed"
    assert submit(client, approval["pending"]).status_code == 409
    assert client.delete(f'/api/chat/approvals/{approval["pending"].id}').status_code == 409
    assert not approval["executed"]


def test_provider_setup_failure_keeps_pending_for_retry(client, db, approval, monkeypatch):
    from app.routers import chat

    def fail(*a):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(chat, "build_provider", fail)
    assert submit(client, approval["pending"]).status_code == 503
    db.refresh(approval["pending"])
    assert approval["pending"].status == "pending" and not approval["executed"]


def test_resume_checks_current_budget_including_paused_usage(client, db, approval, monkeypatch):
    from app import budgets, observability

    checked = []

    def block(db, user, model, **kwargs):
        checked.append((user.id, model, kwargs["additional_spend"]))
        raise HTTPException(402, "budget exceeded")

    monkeypatch.setattr(budgets, "enforce_budget", block)
    monkeypatch.setattr(observability, "compute_cost", lambda *a: 0.5)
    assert submit(client, approval["pending"]).status_code == 402
    assert checked == [("local", "wave2-model", 0.0)]  # usage is already in the ledger
    db.refresh(approval["pending"])
    assert approval["pending"].status == "pending" and not approval["executed"]


@pytest.mark.parametrize("change", ["hidden", "tools_disabled"])
def test_assistant_access_and_capabilities_rechecked(client, db, approval, change):
    assistant = Assistant(name="test", created_by="local", visibility="public")
    db.add(assistant)
    db.flush()
    approval["conv"].assistant_id = assistant.id
    p = approval["pending"]
    p.state = {**p.state, "assistant_id": assistant.id}
    if change == "hidden":
        assistant.is_active = False
    else:
        assistant.capabilities = {"tools": False}
    db.commit()
    response = submit(client, p)
    assert response.status_code == (409 if change == "hidden" else 200)
    assert not approval["executed"]
    db.delete(assistant)
    db.commit()


def test_other_owner_and_admin_cannot_read_or_dismiss(client, db, approval, monkeypatch):
    from app.auth.deps import get_current_user
    from app.main import app
    from app.models import User

    for role in ("user", "admin"):
        app.dependency_overrides[get_current_user] = lambda: User(id="someone-else", role=role)
        try:
            assert client.get(f'/api/chat/approvals/{approval["conv"].id}').status_code == 404
            assert submit(client, approval["pending"]).status_code == 404
            assert client.delete(f'/api/chat/approvals/{approval["pending"].id}').status_code == 404
        finally:
            app.dependency_overrides.pop(get_current_user)


def test_model_error_is_terminal_and_not_replayable(client, db, approval, monkeypatch):
    from app.routers import chat

    class Broken(approval["provider"]):
        def stream(self, *args):
            raise RuntimeError("offline provider failure")
            yield

    monkeypatch.setattr(chat, "build_provider", lambda *a: Broken())
    response = submit(client, approval["pending"])
    assert '"outcome": "failed"' in response.text
    db.refresh(approval["pending"])
    assert approval["pending"].status == "failed"
    assert submit(client, approval["pending"]).status_code == 409
    assert approval["executed"] == [1]


def test_cancelled_resume_does_not_dispatch(client, approval, monkeypatch):
    original = AgentSession.resume

    def cancelled(self, state, decisions):
        self.cancel_event.set()
        yield from original(self, state, decisions)

    monkeypatch.setattr(AgentSession, "resume", cancelled)
    response = submit(client, approval["pending"])
    assert '"outcome": "cancelled"' in response.text and not approval["executed"]


def test_changed_guardrails_block_before_tool_execution(client, db, approval, monkeypatch):
    from app import guardrails

    monkeypatch.setattr(guardrails, "scrub_messages", lambda *a: ([], {"new policy"}, True))
    assert submit(client, approval["pending"]).status_code == 409
    db.refresh(approval["pending"])
    assert approval["pending"].status == "pending" and not approval["executed"]


def test_disappeared_tool_cannot_be_approved(client, approval):
    approval["registry"].unregister("wave2_action")
    response = submit(client, approval["pending"])
    assert response.status_code == 200 and "not enabled" in response.text
    assert not approval["executed"]


def test_claim_rechecks_expiry_after_validation(db, approval):
    from app.approvals import claim, validate_resume

    p = approval["pending"]
    validate_resume(p, {"call-1": "allow"})
    p.created_at = datetime.now(timezone.utc) - timedelta(days=2)
    db.commit()
    with pytest.raises(HTTPException) as err:
        claim(db, p.id)
    assert err.value.status_code == 409
    db.refresh(p)
    assert p.status == "pending"


def test_additive_status_upgrade_preserves_old_snapshots(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, text
    from app.migrations.baseline import add_legacy_columns

    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE pending_approvals (id VARCHAR(32), state JSON)"))
        conn.execute(text("INSERT INTO pending_approvals VALUES ('old', '{}')"))
    with engine.begin() as conn:
        add_legacy_columns(conn)
        add_legacy_columns(conn)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT id, state, status FROM pending_approvals")).one() == (
            "old", "{}", "pending",
        )
    engine.dispose()


def test_budget_gate_counts_additional_pending_spend(monkeypatch):
    from app import budgets
    from app.models import User

    monkeypatch.setattr(budgets, "model_is_priced", lambda model: model == "priced")
    monkeypatch.setattr(budgets, "budget_status", lambda *a: {"budgets": [
        {"limit_usd": 1, "spent_usd": 0.75, "pct": 75, "scope_type": "user"},
    ]})
    user = User(id="local")
    budgets.enforce_budget(None, user, "priced", additional_spend=0.2)
    with pytest.raises(HTTPException) as err:
        budgets.enforce_budget(None, user, "priced", additional_spend=0.25)
    assert err.value.status_code == 402
    budgets.enforce_budget(None, user, "unpriced", additional_spend=10)


def test_dismissal_records_known_usage_once(client, db, approval):
    pid, cid = approval["pending"].id, approval["conv"].id
    assert client.delete(f"/api/chat/approvals/{pid}").status_code == 200
    assert client.delete(f"/api/chat/approvals/{pid}").status_code == 409
    rows = db.query(UsageLedger).filter_by(conversation_id=cid).all()
    assert len(rows) == 1 and rows[0].total_tokens == 10
    assert not approval["executed"]


def test_dismissal_rolls_back_if_usage_cannot_be_recorded(client, db, approval, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app import usage_ledger

    approval["pending"].state = {**approval["pending"].state, "version": 2}
    db.commit()

    def fail(*a, **kw):
        raise RuntimeError("ledger unavailable")

    monkeypatch.setattr(usage_ledger, "record_usage", fail)
    response = TestClient(app, raise_server_exceptions=False).delete(
        f'/api/chat/approvals/{approval["pending"].id}'
    )
    assert response.status_code == 500
    db.refresh(approval["pending"])
    assert approval["pending"].status == "pending"


def test_conversation_deletion_cascades_all_approval_snapshots(client, db):
    cid = client.post("/api/conversations", json={"title": "delete approval history"}).json()["id"]
    ids = []
    for status in ("pending", "completed", "dismissed"):
        p = PendingApproval(conversation_id=cid, status=status, state={"private": "snapshot"})
        db.add(p)
        db.flush()
        ids.append(p.id)
    db.commit()
    assert client.delete(f"/api/conversations/{cid}").status_code == 200
    db.expire_all()
    assert not db.query(PendingApproval).filter(PendingApproval.id.in_(ids)).all()


def test_dismissal_cannot_charge_a_stale_snapshot_after_another_request_finishes(client, db, approval, monkeypatch):
    from sqlalchemy import update
    from app import approvals
    from app.database import SessionLocal

    original = approvals.owned_approval

    def transition(db, pending_id, user):
        row, conv = original(db, pending_id, user)
        with SessionLocal() as other:
            other.execute(update(PendingApproval).where(PendingApproval.id == pending_id)
                          .values(status="failed"))
            other.commit()
        return row, conv

    monkeypatch.setattr(approvals, "owned_approval", transition)
    assert client.delete(f'/api/chat/approvals/{approval["pending"].id}').status_code == 409
    rows = db.query(UsageLedger).filter_by(conversation_id=approval["conv"].id).all()
    assert len(rows) == 1 and rows[0].total_tokens == 10


def test_wave2_snapshot_import_preserves_usage_once(client, db, approval):
    p = approval["pending"]
    state = dict(p.state)
    state["version"] = 2
    state.pop("turn_id")
    state.pop("usage_summary")
    p.state = state
    db.query(UsageLedger).filter_by(conversation_id=approval["conv"].id).delete()
    db.commit()
    response = submit(client, p)
    second_id = next(e["pending_id"] for e in frames(response) if e["type"] == "paused")
    second = db.get(PendingApproval, second_id)
    assert second.state["version"] == 3 and second.state["usage_summary"]["total"] == 20
    assert submit(client, second).status_code == 200
    ledger = db.query(UsageLedger).filter_by(conversation_id=approval["conv"].id).all()
    assert len(ledger) == 3 and sum(r.total_tokens for r in ledger) == 30
    assert sum(r.call_kind == "legacy_resume" for r in ledger) == 1
