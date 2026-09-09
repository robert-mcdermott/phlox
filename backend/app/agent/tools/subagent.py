"""Sub-agent tool: delegate a focused sub-task to a nested agent.

``spawn_subagent`` runs a fresh ``AgentSession`` (ephemeral — it does not pollute the
parent conversation's message history) with a **scoped toolset** in the *same workspace*,
runs it to completion, and returns its final answer as the tool result. This lets the main
agent decompose big tasks (e.g. "research X", "implement Y") and keep its own context lean.

Recursion is prevented by excluding ``spawn_subagent`` from the child's toolset.

The harness can run explicitly read-only children concurrently with a bounded worker pool;
mutation-capable children run sequentially within a turn. This tool opens its **own** DB session
rather than reusing ``ctx.db`` — a SQLAlchemy ``Session`` isn't safe to use from more than
one thread at a time, and ``ctx.db`` is the same shared session across every tool call in
the turn.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.agent.tools.base import Tool, ToolContext, ToolResult

# Tools a sub-agent may use (read/inspect/execute/knowledge — not spawning more sub-agents).
SUBAGENT_TOOLS = {
    "read_file", "write_file", "edit_file", "list_dir", "glob_search", "grep_search",
    "run_shell", "execute_python", "execute_node", "search_documents", "web_fetch",
    "update_todos",
}
READ_ONLY_TOOLS = {
    "read_file", "list_dir", "glob_search", "grep_search", "search_documents", "web_fetch",
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
        "chunks of a larger task (research, implementing one module, analyzing data). "
        "Set read_only=true for inspection/research children that may run concurrently; "
        "otherwise children run sequentially and may use permitted workspace mutation tools."
    )
    category = "planning"
    default_permission = "ask"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "The self-contained task for the sub-agent"},
            "read_only": {
                "type": "boolean", "default": False,
                "description": "Restrict to read-only tools; permits bounded parallel execution.",
            },
        },
        "required": ["task"],
    }

    def run(self, ctx: ToolContext, task: str = "", read_only: bool = False, **_: Any) -> ToolResult:
        if not task.strip():
            return ToolResult(content="No task provided.", is_error=True)
        if not isinstance(read_only, bool):
            return ToolResult(content="read_only must be a boolean.", is_error=True)
        if ctx.cancel_event is not None and ctx.cancel_event.is_set():
            return ToolResult(content="Sub-agent cancelled before dispatch. Not executed.", is_error=True)
        if not ctx.profile or not ctx.model or ctx.allowed_tools is None:
            return ToolResult(content="Sub-agent requires the parent's resolved execution context.",
                              is_error=True)

        # Local imports avoid a circular import (harness imports tools indirectly).
        from app.agent.harness import AgentSession
        from app.agent.permissions import PermissionGate
        from app.agent.registry import REGISTRY
        from app.database import SessionLocal
        from app.models import Assistant, Conversation
        from app.providers.registry import build_provider

        # Own session — see the module docstring on why this can't reuse ctx.db.
        db = SessionLocal()
        try:
            conversation = db.get(Conversation, ctx.conversation_id)
            if conversation is None or conversation.user_id != ctx.user_id:
                return ToolResult(content="Conversation not found.", is_error=True)

            if ctx.assistant_id:
                assistant = db.get(Assistant, ctx.assistant_id)
                if (assistant is None or not assistant.is_active
                        or (assistant.visibility != "public" and assistant.created_by != ctx.user_id)):
                    return ToolResult(content="Assistant is no longer available.", is_error=True)

            profile, model = ctx.profile, ctx.model
            try:
                provider = build_provider(profile, model)
            except Exception as e:  # noqa: BLE001
                return ToolResult(content=f"Sub-agent provider error: {e}", is_error=True)

            params = deepcopy(ctx.params)
            params["max_tool_rounds"] = min(8, max(0, int(params.get("max_tool_rounds", 8))))
            # Inherit the parent turn's real approval state — a sub-agent must not grant
            # itself permissions the user didn't give the parent turn. If the turn isn't
            # in auto-approve mode, ask-tier tools (run_shell, write_file, ...) resolve to
            # "deny" here rather than "ask": a sub-agent runs unattended, so there's no
            # one to answer an approval pause. See PermissionGate(interactive=False).
            gate = PermissionGate(db, REGISTRY, auto_approve=ctx.auto_approve, interactive=False)
            allowed = SUBAGENT_TOOLS & ctx.allowed_tools & gate.enabled_names()
            if read_only:
                allowed &= READ_ONLY_TOOLS

            session = AgentSession(
                db, conversation, provider, REGISTRY, gate, params, profile, model,
                ephemeral=True, allowed_tools=allowed, cancel_event=ctx.cancel_event,
                assistant_id=ctx.assistant_id,
                document_scope=ctx.document_scope,
                tool_observer=ctx.tool_observer,
                accounting=ctx.accounting.child(ctx.parent_call_id) if ctx.accounting else None,
            )
            messages = [
                {"role": "system", "content": SUBAGENT_SYSTEM},
                {"role": "user", "content": task},
            ]
            # Drive the ephemeral session, building the answer from its event stream so
            # we robustly capture output (and surface errors / the last tool result).
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
            return ToolResult(content=content, is_error=err is not None)
        finally:
            db.close()
