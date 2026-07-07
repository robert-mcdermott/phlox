"""Sub-agent must inherit the parent turn's real approval state, not grant itself a
permission bypass. Regression test for the fix in app.agent.tools.subagent.SpawnSubagent:
it used to hardcode PermissionGate(auto_approve=True), so approving a single
spawn_subagent call silently unlocked every ask-tier tool (run_shell, write_file, ...)
inside it, regardless of what the user actually approved for the turn.
"""
from app.agent.permissions import seed_tool_prefs
from app.agent.registry import REGISTRY
from app.agent.tools.base import ToolContext
from app.agent.tools.subagent import SpawnSubagent
from app.models import Conversation
from app.providers.base import StreamDelta, ToolCall
from app.sandbox.runner import get_runner
from app.workspace.manager import workspace_dir


class _RequestsShellThenAnswers:
    """Round 1: ask to run a shell command. Round 2: report what happened."""

    model = "scripted-sub"
    supports_tools = True

    def __init__(self):
        self.round = 0

    def stream(self, messages, tools, params):  # noqa: ARG002
        self.round += 1
        if self.round == 1:
            yield StreamDelta(
                type="tool_calls",
                tool_calls=[ToolCall(id="c1", name="run_shell", arguments={"command": "echo hi"})],
            )
        else:
            # Echo the tool result back so the test can assert on it.
            last_tool_msg = next(m for m in reversed(messages) if m["role"] == "tool")
            yield StreamDelta(type="text", text=f"result: {last_tool_msg['content']}")
            yield StreamDelta(type="done", stop_reason="stop")


def _new_conversation(db):
    conv = Conversation(title="t", user_id="local")
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv


def _run_subagent(db, monkeypatch, *, auto_approve: bool) -> str:
    seed_tool_prefs(db, REGISTRY)  # run_shell -> permission="ask" (its default)
    conv = _new_conversation(db)

    monkeypatch.setattr(
        "app.providers.registry.build_provider", lambda profile, model=None: _RequestsShellThenAnswers()
    )

    ctx = ToolContext(
        conversation_id=conv.id,
        workspace=workspace_dir(conv.id),
        db=db,
        runner=get_runner(),
        user_id=None,
        auto_approve=auto_approve,
    )
    result = SpawnSubagent().run(ctx, task="run a shell command")
    return result.content


def test_subagent_denies_ask_tier_tool_when_parent_turn_is_not_auto_approving(db, monkeypatch):
    content = _run_subagent(db, monkeypatch, auto_approve=False)
    assert "denied" in content.lower()
    assert "not executed" in content.lower()


def test_subagent_allows_ask_tier_tool_when_parent_turn_is_auto_approving(db, monkeypatch):
    content = _run_subagent(db, monkeypatch, auto_approve=True)
    assert "exit 0" in content.lower()
    assert "denied" not in content.lower()
