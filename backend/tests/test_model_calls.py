"""Model-call accounting and final context-fit contracts, entirely offline."""
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.agent.context import ContextLimitError, compact_history, fit_context, request_tokens
from app.model_calls import CallScope, ScopedProvider, stream_model, turn_usage
from app.models import Conversation, Message, UsageLedger
from app.providers.base import StreamDelta, ToolCall, ToolSpec


class Provider:
    model = "meter-model"
    profile_name = "meter-profile"
    supports_tools = True

    def __init__(self, raw=None, *, fail=False):
        self.raw = raw if raw is not None else {"input": 10, "output": 5, "total": 15}
        self.fail = fail
        self.closed = False
        self.seen = None

    def stream(self, messages, tools, params):
        self.seen = messages
        try:
            yield StreamDelta(type="usage", usage=self.raw)
            if self.fail:
                raise RuntimeError("provider failed after reporting usage")
            yield StreamDelta(type="text", text="answer")
            yield StreamDelta(type="done")
        finally:
            self.closed = True


@pytest.fixture
def scope(db, monkeypatch):
    from app import model_calls

    monkeypatch.setattr(model_calls, "snapshot_rate", lambda _: {"input": 2, "output": 4})
    scope = CallScope.new(None, "local")
    yield scope
    db.query(UsageLedger).filter_by(turn_id=scope.turn_id).delete()
    db.commit()


def invoke(provider, scope, messages=None, **kw):
    return list(stream_model(provider, messages or [{"role": "user", "content": "hi"}], [],
                             {"max_tokens": 100, "max_context_tokens": 4000}, scope, **kw))


def rows(db, scope):
    db.expire_all()
    return db.query(UsageLedger).filter_by(turn_id=scope.turn_id).order_by(UsageLedger.created_at).all()


def test_missing_account_cannot_dispatch_when_auth_enabled(db, scope, monkeypatch):
    monkeypatch.setattr("app.config.get_auth_config", lambda: {"enabled": True})
    provider = Provider()
    missing = replace(scope, user_id="deleted-account")
    with pytest.raises(PermissionError, match="no longer active"):
        invoke(provider, missing)
    assert provider.seen is None
    assert not rows(db, scope)


def test_explicit_retry_rechecks_budget(db, scope, monkeypatch):
    from app.model_calls import note_retry

    checked = []

    def gate(*args):
        checked.append(True)
        if len(checked) == 2:
            raise PermissionError("budget changed")

    class RetryProvider(Provider):
        def stream(self, *args):
            note_retry()
            yield StreamDelta(type="done")

    monkeypatch.setattr("app.budgets.enforce_budget", gate)
    with pytest.raises(PermissionError, match="budget changed"):
        invoke(RetryProvider(), scope)
    assert len(checked) == 2
    assert len(rows(db, scope)) == 1
    assert rows(db, scope)[0].status == "failed"


def test_rate_is_snapshotted_and_usage_committed_before_stream_finishes(db, scope, monkeypatch):
    from app import model_calls

    rate = {"input": 2, "output": 4}
    monkeypatch.setattr(model_calls, "get_observability_config", lambda: {"pricing": {"meter-model": rate}})
    # Exercise the actual snapshot function rather than the fixture stub.
    monkeypatch.undo()
    monkeypatch.setattr(model_calls, "get_observability_config", lambda: {"pricing": {"meter-model": rate}})
    source = stream_model(Provider(), [{"role": "user", "content": "hi"}], [], {"max_tokens": 100}, scope)
    assert next(source).type == "usage"
    row = rows(db, scope)[0]
    assert row.total_tokens == 15 and row.status == "running"
    rate["input"] = 1000
    list(source)
    row = rows(db, scope)[0]
    assert row.rate_snapshot == {"input": 2, "output": 4}
    assert row.cost_usd == 0.00004 and row.usage_status == "reported"


@pytest.mark.parametrize("kind", ["failure", "close", "stop"])
def test_partial_usage_survives_failure_close_or_stop(db, scope, kind):
    cancel = threading.Event()
    provider = Provider(fail=kind == "failure")
    source = stream_model(provider, [{"role": "user", "content": "hi"}], [], {"max_tokens": 100},
                          scope, cancel_event=cancel)
    next(source)
    if kind == "failure":
        with pytest.raises(RuntimeError):
            list(source)
    else:
        if kind == "stop":
            cancel.set()
        source.close()
    row = rows(db, scope)[0]
    assert row.total_tokens == 15 and row.cost_usd == 0.00004
    assert row.status == {"failure": "failed", "close": "interrupted", "stop": "cancelled"}[kind]
    assert turn_usage(scope)["cost"] is None and turn_usage(scope)["known_cost"] == 0.00004
    assert provider.closed


@pytest.mark.parametrize("raw,expected,complete", [
    ({"input": 0, "output": 0, "total": 0}, 0, True),
    ({"input": 8}, 8, False), ({"total": 17}, 17, False), ({}, 0, False),
    ({"input": -1, "output": 2}, 0, False),
    ({"input": 1.5, "output": 2}, 0, False),
])
def test_zero_missing_and_invalid_usage_are_distinguished(db, scope, raw, expected, complete):
    invoke(Provider(raw), scope)
    summary = turn_usage(scope)
    assert summary["total"] == expected
    assert (summary["unknown_usage_calls"] == 0) is complete
    assert (summary["cost"] is not None) is complete


def test_cumulative_usage_snapshots_are_not_added(db, scope):
    class Snapshots(Provider):
        def stream(self, *a):
            for output in (1, 3, 3, 7):
                yield StreamDelta(type="usage", usage={"input": 10, "output": output})
            yield StreamDelta(type="done")
    invoke(Snapshots(), scope)
    assert turn_usage(scope)["total"] == 17 and len(rows(db, scope)) == 1


def test_missing_price_is_not_a_free_call(db, scope, monkeypatch):
    monkeypatch.setattr("app.model_calls.snapshot_rate", lambda _: None)
    invoke(Provider(), scope)
    assert turn_usage(scope)["unknown_cost_calls"] == 1
    assert turn_usage(scope)["cost"] is None
    monkeypatch.setattr("app.model_calls.snapshot_rate", lambda _: {"input": 0, "output": 0})
    invoke(Provider(), scope)
    assert rows(db, scope)[1].cost_usd == 0


def test_no_dispatch_when_initial_ledger_insert_fails(scope, monkeypatch):
    from app import model_calls

    provider = Provider()
    def fail(*a, **kw):
        raise RuntimeError("ledger unavailable")
    monkeypatch.setattr(model_calls, "_Call", fail)
    with pytest.raises(RuntimeError, match="ledger unavailable"):
        invoke(provider, scope)
    assert provider.seen is None


def test_explicit_retry_has_separate_attempt_and_parent_metadata(db, scope):
    from app.model_calls import note_retry
    class Retried(Provider):
        def stream(self, *a):
            note_retry()
            yield from super().stream(*a)
    invoke(Retried(), scope)
    first, second = rows(db, scope)
    assert first.status == "failed" and first.usage_status == "unknown"
    assert second.call_kind == "retry" and second.parent_call_id == first.message_id
    assert second.model == "meter-model" and second.profile == "meter-profile"
    assert turn_usage(scope)["unknown_cost_calls"] == 1


def test_fit_reserves_output_counts_schemas_images_and_unicode():
    plain = [{"role": "user", "content": "hello"}]
    tool = ToolSpec("test", "description" * 100, {"type": "object"})
    assert request_tokens(plain, [tool]) > request_tokens(plain)
    assert request_tokens([{**plain[0], "images": ["data:image/png;base64,AAAA"]}]) >= 4096
    assert request_tokens([{"role": "user", "content": "界" * 100}]) > request_tokens([{"role": "user", "content": "a" * 100}])
    with pytest.raises(ContextLimitError):
        fit_context(plain, [tool], {"max_tokens": 900, "max_context_tokens": 1000})


def test_tool_output_trim_preserves_original_history_and_pairing():
    messages = [{"role": "user", "content": "keep me"},
                {"role": "assistant", "tool_calls": [{"id": "t", "name": "read", "arguments": {}}]},
                {"role": "tool", "tool_call_id": "t", "content": "x" * 30000}]
    fitted, info = fit_context(messages, [], {"max_tokens": 100, "max_context_tokens": 1000})
    assert info["trimmed"] and info["input_tokens"] + 100 <= 1000
    assert fitted[0] == messages[0] and fitted[1] == messages[1]
    assert fitted[2]["tool_call_id"] == "t" and "truncated" in fitted[2]["content"]
    assert len(messages[2]["content"]) == 30000


@pytest.mark.parametrize("content,window", [("x" * 20000, None), ("x" * 600, 200)])
def test_oversized_user_or_profile_window_rejected_before_call(db, scope, content, window):
    provider = Provider()
    provider.context_window = window
    with pytest.raises(ContextLimitError):
        invoke(provider, scope, [{"role": "user", "content": content}])
    assert provider.seen is None and not rows(db, scope)


def test_compaction_is_bounded_and_billed_to_same_turn(db, scope):
    provider = Provider()
    history = [{"role": "system", "content": "original"}]
    for _ in range(20):
        history += [{"role": "user", "content": "q" * 1800}, {"role": "assistant", "content": "a" * 1800}]
    compacted, did = compact_history(ScopedProvider(provider, replace(scope, kind="compaction")),
                                    history, 5000, keep_last_turns=1)
    assert did and request_tokens(provider.seen) + 1024 <= 5000
    assert compacted[0] == history[0] and compacted[-2:] == history[-2:]
    assert rows(db, scope)[0].call_kind == "compaction"
    assert turn_usage(scope)["total"] == 15


def test_children_share_parent_turn_without_double_billing(db, monkeypatch):
    from app.agent.harness import AgentSession
    from app.agent.permissions import PermissionGate
    from app.agent.registry import REGISTRY

    class Parent(Provider):
        def stream(self, messages, *args):
            yield StreamDelta(type="usage", usage={"input": 10, "output": 5})
            if not any(m["role"] == "tool" for m in messages):
                yield StreamDelta(type="tool_calls", tool_calls=[
                    ToolCall(f"child-{i}", "spawn_subagent", {"task": "inspect", "read_only": True}) for i in range(2)
                ])
            else:
                yield StreamDelta(type="text", text="Done")
                yield StreamDelta(type="done")

    monkeypatch.setattr("app.providers.registry.build_provider", lambda *a: Provider())
    conv = Conversation(title="children accounting", user_id="local")
    db.add(conv)
    db.commit()
    session = AgentSession(db, conv, Parent(), REGISTRY, PermissionGate(db, REGISTRY, auto_approve=True),
                           {"max_tool_rounds": 3, "max_tokens": 100}, "test", "meter-model",
                           allowed_tools={"spawn_subagent"})
    list(session.run([{"role": "user", "content": "delegate"}]))
    ledger = rows(db, session.accounting)
    assert len(ledger) == 4 and sum(r.total_tokens for r in ledger) == 60
    children = [r for r in ledger if r.call_kind == "child"]
    assert len(children) == 2 and all(r.parent_call_id == ledger[0].message_id for r in children)
    msg = db.query(Message).filter_by(conversation_id=conv.id).one()
    assert msg.usage["total"] == 60
    from app.usage_ledger import backfill_usage_ledger
    backfill_usage_ledger(db)
    assert len(rows(db, session.accounting)) == 4
    assert not db.query(UsageLedger).filter_by(message_id=msg.id).first()
    db.delete(conv)
    db.commit()
    assert len(rows(db, session.accounting)) == 4  # metadata survives content deletion
    db.query(UsageLedger).filter_by(turn_id=session.accounting.turn_id).delete()
    db.commit()


def test_fallback_prices_actual_models_separately(db, scope, monkeypatch):
    from app.agent.harness import AgentSession
    from app.agent.permissions import PermissionGate
    from app.agent.registry import REGISTRY

    monkeypatch.setattr("app.model_calls.snapshot_rate", lambda model: {
        "input": 1 if model == "meter-model" else 9, "output": 1,
    })
    conv = Conversation(user_id="local")
    db.add(conv)
    db.commit()
    fallback = Provider()
    fallback.model = "fallback"
    fallback.profile_name = "fallback-profile"
    session = AgentSession(db, conv, Provider(fail=True), REGISTRY, PermissionGate(db, REGISTRY),
                           {"max_tokens": 100}, "test", "meter-model", fallback_provider=fallback,
                           accounting=scope)
    list(session.run([{"role": "user", "content": "hi"}]))
    first, second = rows(db, scope)
    assert first.model == "meter-model" and first.cost_usd == 0.000015
    assert second.model == "fallback" and second.cost_usd == 0.000095
    assert second.profile == "fallback-profile"
    assert turn_usage(scope)["known_cost"] == 0.00011 and turn_usage(scope)["cost"] is None
    db.delete(conv)
    db.commit()


def test_provider_transport_closed_and_early_usage_preserved(scope, db):
    from app.providers.openai_provider import OpenAIProvider

    class Wire:
        closed = False
        def __iter__(self):
            yield SimpleNamespace(usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2, total_tokens=5), choices=[])
            raise RuntimeError("wire disconnected")
        def close(self):
            self.closed = True

    wire = Wire()
    provider = object.__new__(OpenAIProvider)
    provider.model, provider.supports_tools = "meter-model", False
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: wire)))
    with pytest.raises(RuntimeError, match="wire disconnected"):
        invoke(provider, scope)
    assert wire.closed and rows(db, scope)[0].total_tokens == 5


def test_budget_is_rechecked_before_each_call(scope, db, monkeypatch):
    from fastapi import HTTPException

    def block(*a, **kw):
        raise HTTPException(402, "cap reached")
    monkeypatch.setattr("app.budgets.enforce_budget", block)
    provider = Provider()
    with pytest.raises(HTTPException):
        invoke(provider, scope)
    assert not rows(db, scope) and provider.seen is None


def test_cached_tokens_use_separate_rates_and_require_cache_pricing(db, scope, monkeypatch):
    monkeypatch.setattr("app.model_calls.snapshot_rate", lambda _: {
        "input": 2, "output": 4, "cache_read": 0.1, "cache_write": 3,
    })
    invoke(Provider({"input": 130, "output": 5, "cache_read": 100, "cache_write": 20}), scope)
    assert rows(db, scope)[0].cost_usd == 0.00011
    assert rows(db, scope)[0].usage_details["cache_read"] == 100
    monkeypatch.setattr("app.model_calls.snapshot_rate", lambda _: {"input": 2, "output": 4})
    invoke(Provider({"input": 130, "output": 5, "cache_read": 100}), scope)
    assert rows(db, scope)[1].cost_usd is None
    assert turn_usage(scope)["cost"] is None and turn_usage(scope)["known_cost"] == 0.00011


def test_bedrock_counts_cached_input_and_closes_native_stream(db, scope):
    from app.providers.bedrock_provider import BedrockProvider

    class Wire:
        closed = False
        def __iter__(self):
            yield {"metadata": {"usage": {"inputTokens": 10, "outputTokens": 5,
                  "totalTokens": 15, "cacheReadInputTokens": 100, "cacheWriteInputTokens": 20}}}
            yield {"messageStop": {"stopReason": "end_turn"}}
        def close(self):
            self.closed = True

    wire = Wire()
    provider = object.__new__(BedrockProvider)
    provider.model, provider.supports_tools, provider.prompt_cache = "meter-model", False, False
    provider._client = SimpleNamespace(converse_stream=lambda **kw: {"stream": wire})
    invoke(provider, scope)
    row = rows(db, scope)[0]
    assert row.input_tokens == 130 and row.total_tokens == 135 and wire.closed
    assert row.cost_usd is None  # no cache prices were configured


def test_openai_compatibility_retry_is_metered_separately(db, scope):
    from app.providers.openai_provider import OpenAIProvider

    seen = []
    def create(**kwargs):
        seen.append(kwargs)
        if len(seen) == 1:
            raise RuntimeError("does not support tools")
        return iter([SimpleNamespace(usage=SimpleNamespace(prompt_tokens=4, completion_tokens=2,
                    total_tokens=6, prompt_tokens_details=SimpleNamespace(cached_tokens=2)), choices=[])])
    provider = object.__new__(OpenAIProvider)
    provider.model, provider.supports_tools = "meter-model", True
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    list(stream_model(provider, [{"role": "user", "content": "hi"}],
                      [ToolSpec("noop", "test", {})], {"max_tokens": 100}, scope))
    first, second = rows(db, scope)
    assert len(seen) == 2 and "tools" not in seen[1]
    assert first.status == "failed" and second.status == "completed"
    assert second.total_tokens == 6 and second.usage_details["cache_read"] == 2


def test_usage_views_reconcile_with_ledger_and_distinguish_unknown(client, db, scope, monkeypatch):
    import uuid
    from app.auth.deps import get_current_user
    from app.main import app
    from app.models import User

    user = User(id=uuid.uuid4().hex, role="admin")
    scope = replace(scope, user_id=user.id)
    invoke(Provider(), scope)
    monkeypatch.setattr("app.model_calls.snapshot_rate", lambda _: None)
    invoke(Provider({}), scope)
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        own = client.get("/api/usage").json()
        cell = next(r for r in client.get("/api/usage/by-user").json()["rows"] if r["user_id"] == user.id)
        assert own["total_tokens"] == cell["total_tokens"] == 15
        assert own["cost_usd"] is None and cell["cost_usd"] is None
        assert own["known_cost_usd"] == cell["known_cost_usd"] == 0.00004
        assert own["calls"] == cell["calls"] == 2 and own["turns"] == 1
        assert own["unknown_usage_calls"] == 1
        assert "messages" not in cell and "content" not in cell
    finally:
        app.dependency_overrides.pop(get_current_user)


def test_ledger_metadata_upgrade_is_additive_and_repeatable(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, text
    from app import database

    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    additions = database._ADDED_COLUMNS["usage_ledger"]
    monkeypatch.setattr(database, "ENGINE", engine)
    monkeypatch.setattr(database, "IS_SQLITE", True)
    monkeypatch.setattr(database, "_ADDED_COLUMNS", {"usage_ledger": additions})
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE usage_ledger (id TEXT, cost_usd REAL)"))
        conn.execute(text("INSERT INTO usage_ledger VALUES ('legacy', 1.5)"))
    database._ensure_columns()
    database._ensure_columns()
    with engine.connect() as conn:
        assert conn.execute(text("SELECT id, cost_usd, turn_id, rate_snapshot FROM usage_ledger")).one() == (
            "legacy", 1.5, None, None,
        )
    engine.dispose()
