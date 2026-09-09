"""Bounded research with real harness, scripted models, and isolated source fixtures."""

import json
import threading
import time
from copy import deepcopy

import pytest

from app.agent.harness import AgentSession
from app.agent.permissions import PermissionGate
from app.agent.registry import REGISTRY
from app.agent.tools.base import ToolResult
from app.models import Conversation, Message, PendingApproval
from app.providers.base import StreamDelta, ToolCall
from app.research import Research, normalize_domains


def parse(stream):
    return [json.loads(frame[6:]) for frame in stream]


class ResearchProvider:
    model = "research-fixture"
    supports_tools = True

    def __init__(self, batches=None):
        self.seen = []
        self.batches = list(batches or [[ToolCall("search", "web_search", {"query": "allowance"})]])

    def stream(self, messages, tools, params):
        self.seen.append({"messages": deepcopy(messages), "tools": [t.name for t in tools]})
        yield StreamDelta(type="usage", usage={"input": 30, "output": 10, "total": 40})
        if "planning step" in messages[0]["content"]:
            yield StreamDelta(
                type="text", text="Compare the allowance, effective date, and conflicting policies."
            )
        elif tools and self.batches:
            yield StreamDelta(type="tool_calls", tool_calls=self.batches.pop(0))
        else:
            yield StreamDelta(
                type="text",
                text="Finding: the allowance is $45 [S1]. Conflicting versions need review.",
            )
        yield StreamDelta(type="done", stop_reason="stop")


def session(db, provider, options=None, **kw):
    conv = Conversation(title="Research test", user_id="local")
    db.add(conv)
    db.commit()
    research = Research(options or {"scope": "web", "depth": "brief", "domains": []})
    agent = AgentSession(
        db,
        conv,
        provider,
        REGISTRY,
        PermissionGate(db, REGISTRY, auto_approve=True),
        {"max_tool_rounds": 12},
        "test",
        provider.model,
        research=research,
        **kw,
    )
    return agent, conv


def test_stages_reserve_report_call_and_persist_progress(db, monkeypatch):
    provider = ResearchProvider()
    agent, conv = session(db, provider)
    monkeypatch.setattr(
        REGISTRY.get("web_search"), "run", lambda *a, **kw: ToolResult("No matching results.")
    )
    events = parse(
        agent.run(
            [
                {"role": "system", "content": "Research"},
                {"role": "user", "content": "Compare policies"},
            ]
        )
    )
    assert not provider.seen[0]["tools"] and not provider.seen[-1]["tools"]
    assert set(provider.seen[1]["tools"]) == {"web_search", "web_fetch"}
    assert [e["phase"] for e in events if e["type"] == "research"][0] == "plan"
    msg = db.query(Message).filter_by(conversation_id=conv.id, role="assistant").one()
    assert msg.usage["research"]["phase"] == "completed"
    assert msg.usage["research"]["finished_at"] >= msg.usage["research"]["started_at"]
    assert "unverified" in msg.content
    assert msg.usage["total"] == 40 * len(provider.seen)
    assert not any(
        "Compare the allowance" in e.get("content", "") for e in events if e["type"] == "token"
    )


def test_oversized_batches_and_repeated_queries_are_bounded(db, monkeypatch):
    calls = [ToolCall(str(i), "web_search", {"query": f"query {i}"}) for i in range(100)]
    provider = ResearchProvider([calls] * 20)
    agent, _ = session(db, provider)
    executed = []
    monkeypatch.setattr(
        REGISTRY.get("web_search"),
        "run",
        lambda *a, **kw: executed.append(kw) or ToolResult("discovery"),
    )
    events = parse(
        agent.run([{"role": "system", "content": ""}, {"role": "user", "content": "Research"}])
    )
    assert len(executed) == 3 and len(provider.seen) == 5
    assert provider.seen[-1]["tools"] == []
    assert any(e["type"] == "tool_result" and e["is_error"] for e in events)


def test_document_scope_blocks_web_exec_children_and_mcp(db, monkeypatch):
    calls = [
        ToolCall("w", "web_search", {"query": "private"}),
        ToolCall("f", "web_fetch", {"url": "https://example.com"}),
        ToolCall("x", "run_shell", {"command": "echo SHOULD_NOT_RUN"}),
        ToolCall("m", "mcp_secret", {}),
        ToolCall("c", "spawn_subagent", {"task": "read anything"}),
    ]
    provider = ResearchProvider([calls])
    agent, _ = session(db, provider, {"scope": "documents", "depth": "brief", "domains": []})
    monkeypatch.setattr(
        agent, "_execute_tool_streaming", lambda *a: pytest.fail("Forbidden tool dispatched")
    )
    events = parse(
        agent.run([{"role": "system", "content": ""}, {"role": "user", "content": "Research"}])
    )
    assert all(set(p["tools"]) <= {"search_documents"} for p in provider.seen)
    assert len([e for e in events if e["type"] == "tool_result" and e["is_error"]]) == 5


def test_document_tool_intersects_selected_ids(db, monkeypatch, tmp_path):
    from app.agent.tools.base import ToolContext
    from app.agent.tools.docs import SearchDocuments

    r = Research({"scope": "documents", "depth": "brief", "domains": []}, ["selected"])
    ctx = ToolContext("conv", tmp_path, db, None, research=r)
    received = []
    monkeypatch.setattr(
        "app.agent.tools.docs.search_chunks",
        lambda *a, **kw: received.append(kw["document_ids"]) or [],
    )
    SearchDocuments().run(ctx, query="q")
    SearchDocuments().run(ctx, query="q", document_ids=["selected", "foreign"])
    result = SearchDocuments().run(ctx, query="q", document_ids=["foreign"])
    assert received == [["selected"], ["selected"]] and result.is_error


@pytest.mark.parametrize(
    "value", ["https://example.org", "*.example.org", "example.org/path", "bad..org", "-bad.org"]
)
def test_bad_domain_filters_rejected(value):
    with pytest.raises(ValueError):
        normalize_domains([value])


def test_domain_scope_redirect_is_checked_before_connection(monkeypatch):
    from app import web_fetch

    r = Research({"scope": "web", "depth": "brief", "domains": ["example.org"]})
    assert r.url_allowed("https://docs.example.org/a")
    assert not r.url_allowed("https://example.org.evil.test/a")
    monkeypatch.setattr(
        web_fetch, "checked_addresses", lambda *a: pytest.fail("Out-of-scope DNS lookup")
    )
    with pytest.raises(web_fetch.FetchError, match="outside"):
        web_fetch.fetch("https://evil.test/", url_policy=r.url_allowed)


def test_stop_prevents_synthesis_and_further_actions(db, monkeypatch):
    cancel = threading.Event()
    provider = ResearchProvider()
    agent, conv = session(db, provider, cancel_event=cancel)

    def stop(*a, **kw):
        cancel.set()
        return ToolResult("Stopped")

    monkeypatch.setattr(REGISTRY.get("web_search"), "run", stop)
    parse(agent.run([{"role": "system", "content": ""}, {"role": "user", "content": "Research"}]))
    assert len(provider.seen) == 2
    msg = db.query(Message).filter_by(conversation_id=conv.id).one()
    assert msg.usage["research"]["phase"] == "cancelled"
    assert "before a complete report" in msg.content


def test_expired_research_goes_directly_to_report(db):
    provider = ResearchProvider()
    agent, _ = session(db, provider)
    agent.research.state["started_at"] = time.time() - 1000
    parse(agent.run([{"role": "system", "content": ""}, {"role": "user", "content": "Research"}]))
    assert len(provider.seen) == 1 and provider.seen[0]["tools"] == []


def test_approval_preserves_research_counters_and_scope(db, monkeypatch):
    provider = ResearchProvider()
    agent, conv = session(db, provider)
    agent.gate.auto_approve = False
    monkeypatch.setattr(agent.gate, "decide", lambda name: "ask")
    parse(agent.run([{"role": "system", "content": ""}, {"role": "user", "content": "Research"}]))
    pending = db.query(PendingApproval).filter_by(conversation_id=conv.id).one()
    assert pending.state["research"]["phase"] == "gather"
    assert pending.state["rounds_used"] == 2
    resumed = AgentSession(
        db,
        conv,
        provider,
        REGISTRY,
        PermissionGate(db, REGISTRY, auto_approve=True),
        {"max_tool_rounds": 12},
        "test",
        provider.model,
    )
    monkeypatch.setattr(REGISTRY.get("web_search"), "run", lambda *a, **kw: ToolResult("discovery"))
    parse(resumed.resume(pending.state, {"search": "allow"}))
    assert resumed.research.state["searches"] == 1
    assert resumed.allowed_tools == {"web_search", "web_fetch"}
    assert resumed.rounds_used <= 5


@pytest.mark.parametrize("arguments", [None, [], "broken JSON", {"query": 10}, {}])
def test_invalid_tool_arguments_never_dispatch(db, monkeypatch, arguments):
    provider = ResearchProvider([[ToolCall("bad", "web_search", arguments)]])
    agent, _ = session(db, provider)
    monkeypatch.setattr(
        REGISTRY.get("web_search"), "run", lambda *a, **kw: pytest.fail("Invalid dispatch")
    )
    events = parse(
        agent.run([{"role": "system", "content": ""}, {"role": "user", "content": "Research"}])
    )
    assert any("Invalid arguments" in e.get("content", "") for e in events)


def test_api_chat_default_and_explicit_research_do_not_leak_old_context(client, monkeypatch):
    providers = []

    def build(*a):
        p = ResearchProvider([])
        providers.append(p)
        return p

    monkeypatch.setattr("app.routers.chat.build_provider", build)
    monkeypatch.setattr("app.routers.chat._build_fallback", lambda *a: None)
    response = client.post("/api/chat", json={"message": "PRIVATE_OLD_CONTEXT"})
    events = [json.loads(x[6:]) for x in response.text.splitlines() if x.startswith("data: ")]
    conv = next(e["id"] for e in events if e["type"] == "conversation")
    assert not any(e["type"] == "research" for e in events)
    monkeypatch.setattr(
        REGISTRY.get("web_search"), "run", lambda *a, **kw: ToolResult("no results")
    )
    response = client.post(
        "/api/chat",
        json={
            "conversation_id": conv,
            "message": "Research current policy",
            "research": {"scope": "web"},
        },
    )
    assert response.status_code == 200 and '"type": "research"' in response.text
    assert all("PRIVATE_OLD_CONTEXT" not in json.dumps(p) for p in providers[-1].seen)
    bad = client.post("/api/chat", json={"message": "Research", "research": {"scope": "documents"}})
    assert bad.status_code == 422


@pytest.mark.parametrize("durable", [False, True])
def test_actual_web_evidence_report_export_and_durable_replay(db, client, monkeypatch, durable):
    import hashlib
    import uuid
    from types import SimpleNamespace
    from app import runs, web_fetch
    from app.models import Source

    provider = ResearchProvider(
        [[ToolCall("fetch", "web_fetch", {"url": "https://example.org/policy"})]]
    )
    monkeypatch.setattr("app.routers.chat.build_provider", lambda *a: provider)
    monkeypatch.setattr("app.routers.chat._build_fallback", lambda *a: None)
    monkeypatch.setattr(
        web_fetch,
        "fetch",
        lambda url, cancel, url_policy=None: web_fetch.Page(
            url,
            "Policy",
            "The meal allowance is $45.",
            hashlib.sha256(b"policy").hexdigest(),
            False,
            200,
        ),
    )
    monkeypatch.setattr("app.config.runs_enabled", lambda: durable)
    monkeypatch.setattr(runs, "runs_enabled", lambda: durable)
    worker = runs.Worker()
    worker.thread = SimpleNamespace(is_alive=lambda: True)
    monkeypatch.setattr(runs, "worker", worker)
    conv = Conversation(title="Research evidence test", user_id="local")
    db.add(conv)
    db.commit()
    payload = {
        "conversation_id": conv.id,
        "message": "Find the allowance",
        "research": {"scope": "web", "depth": "brief", "domains": ["example.org"]},
    }
    if durable:
        result = client.post(
            "/api/runs", json=payload, headers={"Idempotency-Key": uuid.uuid4().hex}
        )
        assert result.status_code == 200, result.text
        worker.step()
        replay = client.get("/api/runs/" + result.json()["id"] + "/events").text
        assert '"phase": "plan"' in replay and '"phase": "completed"' in replay
    else:
        assert client.post("/api/chat", json=payload).status_code == 200
    messages = client.get(f"/api/conversations/{conv.id}").json()["messages"]
    report = messages[-1]
    assert report["citations"][0]["source_id"]
    assert report["usage"]["research"]["source_count"] == 1
    assert messages[0]["attachments"][0]["type"] == "research"
    assert db.query(Source).filter_by(conversation_id=conv.id).count() == 1
    export = client.get(f"/api/conversations/{conv.id}/export").json()["markdown"]
    assert "meal allowance is $45" in export and "https://example.org/policy" in export


def test_skill_does_not_enable_research(client, monkeypatch):
    providers = []

    def build(*a):
        provider = ResearchProvider()
        providers.append(provider)
        return provider

    monkeypatch.setattr("app.routers.chat.build_provider", build)
    monkeypatch.setattr("app.routers.chat._build_fallback", lambda *a: None)
    response = client.post("/api/chat", json={"message": "Summarize", "skills": ["deep-research"]})
    assert response.status_code == 200 and '"type": "research"' not in response.text


def test_regenerate_restores_research_mode(client, monkeypatch):
    seen = []

    def build(*a):
        p = ResearchProvider()
        seen.append(p)
        return p

    monkeypatch.setattr("app.routers.chat.build_provider", build)
    monkeypatch.setattr("app.routers.chat._build_fallback", lambda *a: None)
    monkeypatch.setattr(
        REGISTRY.get("web_search"), "run", lambda *a, **kw: ToolResult("No evidence")
    )
    response = client.post(
        "/api/chat",
        json={"message": "Research this", "research": {"scope": "web", "domains": ["example.org"]}},
    )
    frames = [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]
    conv = next(e["id"] for e in frames if e["type"] == "conversation")
    response = client.post("/api/chat", json={"conversation_id": conv, "regenerate": True})
    assert '"type": "research"' in response.text
    assert not seen[-1].seen[0]["tools"]
    assert all(set(round["tools"]) <= {"web_search", "web_fetch"} for round in seen[-1].seen)


def test_schema_validation_never_fetches_remote_refs(monkeypatch):
    from types import SimpleNamespace
    from app.agent.validation import argument_error

    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *a, **kw: pytest.fail("Remote schema lookup")
    )
    tool = SimpleNamespace(name="remote", parameters={"$ref": "https://example.org/schema"})
    assert "could not be validated locally" in argument_error(tool, {})
    tool.parameters = {
        "$defs": {"query": {"type": "string"}},
        "type": "object",
        "properties": {"query": {"$ref": "#/$defs/query"}},
    }
    assert argument_error(tool, {"query": "ok"}) is None
    assert argument_error(tool, {"query": 3})
