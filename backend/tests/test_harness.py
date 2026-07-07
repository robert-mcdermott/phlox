"""Integration/eval tests for the agent loop using scripted fake providers."""
import threading
import time

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
        yield  # pragma: no cover - makes this a generator


class TextProvider:
    model = "fallback-model"
    supports_tools = True

    def stream(self, messages, tools, params):
        yield StreamDelta(type="text", text="Answer from the fallback model.")
        yield StreamDelta(type="done", stop_reason="stop")


class UnexpectedToolProvider:
    model = "unexpected"
    supports_tools = True

    def __init__(self):
        self.round = 0

    def stream(self, messages, tools, params):  # noqa: ARG002
        self.round += 1
        if self.round == 1:
            yield StreamDelta(
                type="tool_calls",
                tool_calls=[ToolCall(id="c1", name="web_search", arguments={"query": "latest"})],
            )
        else:
            yield StreamDelta(type="text", text="Done.")
            yield StreamDelta(type="done", stop_reason="stop")


class ShellProvider:
    """Round 1: request run_shell with a caller-supplied command. Round 2: final text."""

    model = "shell-scripted"
    supports_tools = True

    def __init__(self, command: str, timeout: int = 60):
        self.command = command
        self.timeout = timeout
        self.round = 0

    def stream(self, messages, tools, params):  # noqa: ARG002
        self.round += 1
        if self.round == 1:
            yield StreamDelta(
                type="tool_calls",
                tool_calls=[
                    ToolCall(id="c1", name="run_shell", arguments={"command": self.command, "timeout": self.timeout})
                ],
            )
        else:
            yield StreamDelta(type="text", text="Ran it.")
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


def test_allowed_tools_denies_unadvertised_tool_calls(db):
    conv = _new_conversation(db)
    session = _session(db, conv, UnexpectedToolProvider(), allowed_tools=set())
    events = "".join(session.run([{"role": "system", "content": "s"},
                                  {"role": "user", "content": "search"}]))

    assert "web_search" in events
    assert "not enabled for this turn" in events


def test_tool_progress_events_stream_live_shell_output(db):
    conv = _new_conversation(db)
    session = _session(db, conv, ShellProvider("echo hello-world"))
    events = "".join(session.run([{"role": "system", "content": "s"},
                                  {"role": "user", "content": "run it"}]))

    assert "tool_progress" in events
    assert "hello-world" in events


def test_cancel_event_set_before_run_skips_provider_entirely(db):
    # If the turn is already cancelled before the first round, the loop must finalize
    # without ever calling the model (and without hanging).
    conv = _new_conversation(db)
    provider = ScriptedProvider()
    cancel = threading.Event()
    cancel.set()
    session = _session(db, conv, provider, cancel_event=cancel)

    events = "".join(session.run([{"role": "system", "content": "s"},
                                  {"role": "user", "content": "hi"}]))

    assert provider.round == 0
    assert '"type": "done"' in events


def test_cancel_event_kills_in_flight_shell_command_promptly(db):
    # Regression test: cancelling a turn must actually kill the running subprocess (and
    # its children) rather than leaving the harness blocked until the command's own
    # timeout — see the process-tree-kill fix in app.sandbox.runner.
    conv = _new_conversation(db)
    cancel = threading.Event()
    provider = ShellProvider("echo start; sleep 5; echo end", timeout=30)
    session = _session(db, conv, provider, cancel_event=cancel)

    def canceller():
        time.sleep(0.3)
        cancel.set()

    threading.Thread(target=canceller, daemon=True).start()

    start = time.monotonic()
    events = "".join(session.run([{"role": "system", "content": "s"},
                                  {"role": "user", "content": "run it"}]))
    elapsed = time.monotonic() - start

    assert elapsed < 3.0, f"cancellation took {elapsed:.1f}s — the child process wasn't killed promptly"
    assert "start" in events
    # "end" appears once already, in the echoed tool-call arguments (the command string
    # itself) — it must not appear again as actual output, which would mean the sleep
    # (and the echo after it) ran to completion instead of being killed.
    assert events.count("end") == 1
    assert "cancelled" in events.lower()


class TwoSubagentsProvider:
    """Round 1: request two spawn_subagent calls at once. Round 2: final text."""

    model = "two-subagents"
    supports_tools = True

    def __init__(self):
        self.round = 0

    def stream(self, messages, tools, params):  # noqa: ARG002
        self.round += 1
        if self.round == 1:
            yield StreamDelta(
                type="tool_calls",
                tool_calls=[
                    ToolCall(id="c1", name="spawn_subagent", arguments={"task": "task A"}),
                    ToolCall(id="c2", name="spawn_subagent", arguments={"task": "task B"}),
                ],
            )
        else:
            yield StreamDelta(type="text", text="Both done.")
            yield StreamDelta(type="done", stop_reason="stop")


class SlowSubagentProvider:
    """Stands in for each sub-agent's own model call: takes a bit of real wall-clock
    time, so sequential-vs-concurrent execution is distinguishable by elapsed time."""

    model = "slow-subagent"
    supports_tools = True

    def stream(self, messages, tools, params):  # noqa: ARG002
        time.sleep(0.3)
        yield StreamDelta(type="text", text="subagent result")
        yield StreamDelta(type="done", stop_reason="stop")


def test_concurrent_spawn_subagent_calls_run_in_parallel(db, monkeypatch):
    # Regression test for the fix in AgentSession._run_calls_concurrently: two
    # spawn_subagent calls requested in the same round used to run one after another;
    # they should now run in parallel worker threads.
    conv = _new_conversation(db)
    monkeypatch.setattr(
        "app.providers.registry.build_provider", lambda profile, model=None: SlowSubagentProvider()
    )

    session = _session(db, conv, TwoSubagentsProvider())
    start = time.monotonic()
    events = "".join(session.run([{"role": "system", "content": "s"},
                                  {"role": "user", "content": "do both"}]))
    elapsed = time.monotonic() - start

    assert elapsed < 0.55, f"took {elapsed:.2f}s — looks sequential (2x0.3s), not concurrent"
    assert events.count("subagent result") == 2
