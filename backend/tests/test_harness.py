"""Integration/eval tests for the agent loop using scripted fake providers."""
from app.providers.base import StreamDelta, ToolCall


class ScriptedProvider:
    """Round 1: request write_file. Round 2: final text. Reports token usage."""

    model = "scripted"
    supports_tools = True

    def __init__(self):
        self.round = 0

    def stream(self, messages, tools, params):
        self.round += 1
        if self.round == 1:
            yield StreamDelta(type="usage", usage={"input": 10, "output": 5, "total": 15})
            yield StreamDelta(
                type="tool_calls",
                tool_calls=[ToolCall(id="c1", name="write_file",
                                     arguments={"path": "hello.txt", "content": "hi there"})],
            )
        else:
            yield StreamDelta(type="text", text="Wrote the file.")
            yield StreamDelta(type="usage", usage={"input": 20, "output": 3, "total": 23})
            yield StreamDelta(type="done", stop_reason="stop")


class FailingProvider:
    model = "failing"
    supports_tools = True

    def stream(self, messages, tools, params):
        raise RuntimeError("primary down")
        yield  # noqa: unreachable — makes this a generator


class TextProvider:
    model = "fallback-model"
    supports_tools = True

    def stream(self, messages, tools, params):
        yield StreamDelta(type="text", text="Answer from the fallback model.")
        yield StreamDelta(type="done", stop_reason="stop")


def _new_conversation(db):
    from app.models import Conversation

    conv = Conversation(title="t", user_id="local")
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv


def _session(db, conv, provider, **kw):
    from app.agent.harness import AgentSession
    from app.agent.permissions import PermissionGate
    from app.agent.registry import REGISTRY

    gate = PermissionGate(db, REGISTRY, auto_approve=True)
    return AgentSession(db, conv, provider, REGISTRY, gate, {"max_tool_rounds": 4}, "test", "m", **kw)


def test_agent_loop_executes_tool_and_persists_usage(db):
    from app.workspace.manager import workspace_dir

    conv = _new_conversation(db)
    session = _session(db, conv, ScriptedProvider())
    events = "".join(session.run([{"role": "system", "content": "s"},
                                  {"role": "user", "content": "make a file"}]))

    # The tool actually ran and created the file in the workspace.
    assert (workspace_dir(conv.id) / "hello.txt").read_text() == "hi there"

    # Assistant turn persisted with the tool step and accumulated usage (15 + 23).
    db.refresh(conv)
    asst = [m for m in conv.messages if m.role == "assistant"]
    assert asst and asst[-1].tool_calls[0]["name"] == "write_file"
    assert asst[-1].usage["total"] == 38

    # Event stream surfaced the tool call/result and a done event.
    assert "write_file" in events and "tool_result" in events and '"type": "done"' in events


def test_fallback_provider_used_on_primary_failure(db):
    conv = _new_conversation(db)
    fallback = TextProvider()
    session = _session(db, conv, FailingProvider(), fallback_provider=fallback)
    events = "".join(session.run([{"role": "system", "content": "s"},
                                  {"role": "user", "content": "hi"}]))

    assert session.provider is fallback  # swapped after the primary failed
    db.refresh(conv)
    asst = [m for m in conv.messages if m.role == "assistant"]
    assert asst and "fallback model" in asst[-1].content.lower()
    assert "switching to fallback" in events.lower()
