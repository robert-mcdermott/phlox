"""Delegation must narrow a resolved parent context, never select global defaults."""
import json
import threading
import time
import uuid
from dataclasses import replace

import pytest

from app.agent.harness import AgentSession, MAX_CONCURRENT_SUBAGENTS, MAX_SUBAGENTS_PER_ROUND
from app.agent.permissions import PermissionGate
from app.agent.registry import REGISTRY
from app.agent.tools.base import ToolResult
from app.agent.tools.subagent import SpawnSubagent
from app.models import Assistant, Conversation
from app.providers.base import StreamDelta, ToolCall


class CallsThenAnswer:
    supports_tools = True

    def __init__(self, calls=(), *, model="parent-model", profile_name=None):
        self.calls = list(calls)
        self.model = model
        self.profile_name = profile_name
        self.round = 0

    def stream(self, messages, tools, params):
        self.round += 1
        if self.round == 1 and self.calls:
            yield StreamDelta(type="tool_calls", tool_calls=self.calls)
        else:
            results = [m["content"] for m in messages if m["role"] == "tool"]
            yield StreamDelta(type="text", text=" | ".join(results) or "child answer")
            yield StreamDelta(type="done")


def children(count, *, read_only=True):
    return [ToolCall(f"child-{i}", "spawn_subagent", {"task": str(i), "read_only": read_only})
            for i in range(count)]


@pytest.fixture
def parent(db):
    conversations = []

    def build(provider=None, *, owner=None, allowed=None, profile="parent-profile", cancel=None,
              assistant_id=None):
        conv = Conversation(title="delegation fixture", user_id=owner or uuid.uuid4().hex)
        db.add(conv)
        db.commit()
        conversations.append(conv)
        return AgentSession(
            db, conv, provider or CallsThenAnswer(children(1)), REGISTRY,
            PermissionGate(db, REGISTRY, auto_approve=True),
            {"max_tool_rounds": 4, "temperature": 0.7, "max_tokens": 321},
            profile, "stale-request-model", allowed_tools=allowed,
            cancel_event=cancel, assistant_id=assistant_id,
        )

    yield build
    for conv in conversations:
        db.delete(conv)
    db.commit()


def run(session):
    return list(session.run([{"role": "user", "content": "delegate"}]))


def test_two_users_children_keep_resolved_parent_models_and_parameters(parent, monkeypatch):
    seen = []

    def no_defaults(*args, **kwargs):
        raise AssertionError("a child must not resolve global or per-user defaults again")

    monkeypatch.setattr("app.runtime_settings.get_settings", no_defaults)

    def build_provider(profile, model):
        class Inspect(CallsThenAnswer):
            def stream(self, messages, tools, params):
                seen.append((profile, model, dict(params), {t.name for t in tools}))
                yield from super().stream(messages, tools, params)
        return Inspect(model=model)

    monkeypatch.setattr("app.providers.registry.build_provider", build_provider)
    for user in ("user-one", "user-two"):
        provider = CallsThenAnswer(children(1), model=f"{user}-chosen")
        session = parent(provider, owner=user, profile=f"{user}-profile",
                         allowed={"spawn_subagent", "read_file"})
        assert "child answer" in "".join(run(session))
    assert [(s[0], s[1]) for s in seen] == [
        ("user-one-profile", "user-one-chosen"), ("user-two-profile", "user-two-chosen")]
    assert all(s[2] == {"max_tool_rounds": 4, "temperature": 0.7, "max_tokens": 321}
               and s[3] == {"read_file"} for s in seen)


@pytest.mark.parametrize("tool,arguments", [
    ("search_documents", {"query": "private"}),
    ("write_file", {"path": "forbidden.txt", "content": "no"}),
    ("run_shell", {"command": "echo no"}),
    ("update_todos", {"todos": []}),
    ("spawn_subagent", {"task": "recurse"}),
])
def test_readonly_child_cannot_invoke_hidden_or_mutating_tools(parent, monkeypatch, tool, arguments):
    session = parent(allowed={"spawn_subagent", "write_file", "run_shell", "update_todos"})
    monkeypatch.setattr("app.providers.registry.build_provider", lambda *args: CallsThenAnswer(
        [ToolCall("forbidden", tool, arguments)]))
    text = "".join(run(session))
    assert "not enabled for this turn" in text
    assert not (session.workspace / "forbidden.txt").exists()
    assert not (session.workspace / ".phlox/todos.json").exists()


def test_mutating_child_still_cannot_widen_parent_document_scope(parent, monkeypatch):
    session = parent(CallsThenAnswer(children(1, read_only=False)), allowed={"spawn_subagent"})
    monkeypatch.setattr("app.providers.registry.build_provider", lambda *args: CallsThenAnswer(
        [ToolCall("search", "search_documents", {"query": "hidden"})]))
    assert "not enabled for this turn" in "".join(run(session))


def test_owner_scope_and_cancellation_are_carried_into_child_tools(parent, monkeypatch):
    cancel = threading.Event()
    session = parent(cancel=cancel, allowed={"spawn_subagent", "read_file"})
    seen = []

    def read(ctx, **kwargs):
        seen.append((ctx.user_id, ctx.assistant_id, ctx.cancel_event))
        return ToolResult("inspected")

    monkeypatch.setattr(REGISTRY.get("read_file"), "run", read)
    monkeypatch.setattr("app.providers.registry.build_provider", lambda *args: CallsThenAnswer(
        [ToolCall("read", "read_file", {"path": "anything"})]))
    run(session)
    assert seen == [(session.conversation.user_id, None, cancel)]


def test_missing_context_and_wrong_owner_fail_before_provider_dispatch(parent, monkeypatch):
    session = parent()

    def unexpected(*args):
        raise AssertionError("must not construct a provider")

    monkeypatch.setattr("app.providers.registry.build_provider", unexpected)
    for ctx in (replace(session.ctx, profile=None), replace(session.ctx, allowed_tools=None),
                replace(session.ctx, user_id="different-owner")):
        assert SpawnSubagent().run(ctx, task="inspect").is_error


def test_revoked_assistant_scope_is_not_inherited(parent, db, monkeypatch):
    owner = uuid.uuid4().hex
    assistant = Assistant(name="revoked", visibility="private", created_by=owner, is_active=False)
    db.add(assistant)
    db.commit()
    try:
        session = parent(owner=owner, assistant_id=assistant.id)
        result = SpawnSubagent().run(session.ctx, task="inspect")
        assert result.is_error and "no longer available" in result.content
    finally:
        db.delete(assistant)
        db.commit()


def test_visible_assistant_scope_reaches_child_retrieval(parent, db, monkeypatch):
    owner = uuid.uuid4().hex
    assistant = Assistant(name="scoped fixture", visibility="private", created_by=owner,
                          is_active=True)
    db.add(assistant)
    db.commit()
    try:
        session = parent(owner=owner, assistant_id=assistant.id,
                         allowed={"spawn_subagent", "search_documents"})
        seen = []

        def search(ctx, **kwargs):
            seen.append((ctx.user_id, ctx.assistant_id))
            return ToolResult("scoped passage")

        monkeypatch.setattr(REGISTRY.get("search_documents"), "run", search)
        monkeypatch.setattr("app.providers.registry.build_provider", lambda *args: CallsThenAnswer(
            [ToolCall("search", "search_documents", {"query": "authorized"})]))
        run(session)
        assert seen == [(owner, assistant.id)]
    finally:
        db.delete(assistant)
        db.commit()


def test_delegation_follows_fallback_route(parent, monkeypatch):
    class Failing(CallsThenAnswer):
        def stream(self, *args):
            raise RuntimeError("primary unavailable")
            yield

    session = parent(Failing(), allowed={"spawn_subagent"})
    session.fallback_provider = CallsThenAnswer(children(1), model="fallback-chosen",
                                               profile_name="fallback-profile")
    seen = []

    def build(profile, model):
        seen.append((profile, model))
        return CallsThenAnswer(model=model)

    monkeypatch.setattr("app.providers.registry.build_provider", build)
    run(session)
    assert seen == [("fallback-profile", "fallback-chosen")]


def test_registry_records_resolved_profile():
    from app.providers.registry import build_provider

    provider = build_provider("test", "chosen")
    assert provider.profile_name == "test" and provider.model == "chosen"


def test_child_provider_errors_are_tool_errors(parent, monkeypatch):
    class Failing(CallsThenAnswer):
        def stream(self, *args):
            raise RuntimeError("child unavailable")
            yield

    monkeypatch.setattr("app.providers.registry.build_provider", lambda *args: Failing())
    result = SpawnSubagent().run(parent().ctx, task="inspect")
    assert result.is_error and "child unavailable" in result.content


@pytest.mark.parametrize("cancel_queued", [False, True])
def test_readonly_worker_limit_and_queued_cancellation(parent, monkeypatch, cancel_queued):
    cancel = threading.Event()
    session = parent(CallsThenAnswer(children(8)), cancel=cancel, allowed={"spawn_subagent"})
    lock = threading.Lock()
    saturated, release = threading.Event(), threading.Event()
    active = peak = 0
    started = []

    def child(ctx, task, **kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(active, peak)
            started.append(task)
            if active == MAX_CONCURRENT_SUBAGENTS:
                saturated.set()
        assert release.wait(2)
        with lock:
            active -= 1
        return ToolResult(f"result-{task}")

    monkeypatch.setattr(REGISTRY.get("spawn_subagent"), "run", child)
    output = []
    thread = threading.Thread(target=lambda: output.extend(run(session)), daemon=True)
    thread.start()
    try:
        assert saturated.wait(2)
        if cancel_queued:
            cancel.set()
    finally:
        release.set()
        thread.join(3)
    assert not thread.is_alive()
    assert peak == MAX_CONCURRENT_SUBAGENTS
    assert len(started) == (MAX_CONCURRENT_SUBAGENTS if cancel_queued else 8)
    if cancel_queued:
        assert "cancelled before dispatch" in "".join(output)


def test_excess_child_requests_are_rejected_without_dispatch(parent, monkeypatch):
    session = parent(CallsThenAnswer(children(MAX_SUBAGENTS_PER_ROUND + 2)),
                     allowed={"spawn_subagent"})
    started = []

    def child(ctx, task, **kwargs):
        started.append(task)
        return ToolResult("done")

    monkeypatch.setattr(REGISTRY.get("spawn_subagent"), "run", child)
    output = run(session)
    events = [json.loads(frame.removeprefix("data: ")) for frame in output]
    errors = [e for e in events if e["type"] == "tool_result" and e["is_error"]]
    assert len(started) == MAX_SUBAGENTS_PER_ROUND
    assert len(errors) == 2 and all("Sub-agent limit" in e["content"] for e in errors)


def test_mutating_children_are_sequential(parent, monkeypatch):
    session = parent(CallsThenAnswer(children(3, read_only=False)), allowed={"spawn_subagent"})
    path = session.workspace / "counter.txt"
    path.write_text("0")
    active = peak = 0

    def child(ctx, task, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        previous = int(path.read_text())
        time.sleep(0.02)  # a read-modify-write cycle would lose updates if children overlap
        path.write_text(str(previous + 1))
        active -= 1
        return ToolResult(task)

    monkeypatch.setattr(REGISTRY.get("spawn_subagent"), "run", child)
    run(session)
    assert peak == 1 and path.read_text() == "3"
