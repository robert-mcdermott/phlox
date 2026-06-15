"""Sub-agent tool: delegate a focused sub-task to a nested agent.

``spawn_subagent`` runs a fresh ``AgentSession`` (ephemeral — it does not pollute the
parent conversation's message history) with a **scoped toolset** in the *same workspace*,
runs it to completion, and returns its final answer as the tool result. This lets the main
agent decompose big tasks (e.g. "research X", "implement Y") and keep its own context lean.

Recursion is prevented by excluding ``spawn_subagent`` from the child's toolset.
"""
from __future__ import annotations

from typing import Any

from app.agent.tools.base import Tool, ToolContext, ToolResult

# Tools a sub-agent may use (read/inspect/execute/knowledge — not spawning more sub-agents).
SUBAGENT_TOOLS = {
    "read_file", "write_file", "edit_file", "list_dir", "glob_search", "grep_search",
    "run_shell", "execute_python", "execute_node", "search_documents", "web_fetch",
    "update_todos",
}

SUBAGENT_SYSTEM = (
    "You are a focused sub-agent working in a shared workspace. Complete the assigned task "
    "using the available tools, then reply with a concise report of what you did and the "
    "key results. Do not ask follow-up questions; make reasonable assumptions."
)


class SpawnSubagent(Tool):
    name = "spawn_subagent"
    description = (
        "Delegate a focused, self-contained sub-task to a nested agent that shares this "
        "workspace and reports back its result. Use for parallelizable or well-scoped "
        "chunks of a larger task (research, implementing one module, analyzing data)."
    )
    category = "planning"
    default_permission = "ask"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "The self-contained task for the sub-agent"},
        },
        "required": ["task"],
    }

    def run(self, ctx: ToolContext, task: str = "", **_: Any) -> ToolResult:
        if not task.strip():
            return ToolResult(content="No task provided.", is_error=True)

        # Local imports avoid a circular import (harness imports tools indirectly).
        from app.agent.harness import AgentSession
        from app.agent.permissions import PermissionGate
        from app.agent.registry import REGISTRY
        from app.models import Conversation
        from app.providers.registry import build_provider
        from app.runtime_settings import generation_params, get_settings

        conversation = ctx.db.get(Conversation, ctx.conversation_id)
        if conversation is None:
            return ToolResult(content="Conversation not found.", is_error=True)

        settings = get_settings(ctx.db)
        profile = settings["active_profile"]
        model = settings.get("model")
        try:
            provider = build_provider(profile, model)
        except Exception as e:  # noqa: BLE001
            return ToolResult(content=f"Sub-agent provider error: {e}", is_error=True)

        params = {**generation_params(settings), "max_tool_rounds": 8}
        gate = PermissionGate(ctx.db, REGISTRY, auto_approve=True)  # scoped + auto within the sub-task
        allowed = SUBAGENT_TOOLS & set(REGISTRY.names())

        session = AgentSession(
            ctx.db, conversation, provider, REGISTRY, gate, params, profile, model,
            ephemeral=True, allowed_tools=allowed,
        )
        messages = [
            {"role": "system", "content": SUBAGENT_SYSTEM},
            {"role": "user", "content": task},
        ]
        # Drive the ephemeral session, building the answer from its event stream so we
        # robustly capture output (and surface errors / the last tool result).
        import json as _json

        answer = ""
        err: str | None = None
        last_tool: str | None = None
        for frame in session.run(messages):
            line = frame.strip()
            if not line.startswith("data: "):
                continue
            try:
                ev = _json.loads(line[6:])
            except ValueError:
                continue
            if ev["type"] == "token":
                answer += ev.get("content", "")
            elif ev["type"] == "error":
                err = ev.get("content")
            elif ev["type"] == "tool_result":
                last_tool = ev.get("content")

        content = answer.strip() or session.final_text.strip()
        if not content:
            if err:
                content = f"Sub-agent error: {err}"
            elif last_tool:
                content = f"Sub-agent ran tools. Last result:\n{last_tool}"
            else:
                content = "(sub-agent produced no output)"
        return ToolResult(content=content)
