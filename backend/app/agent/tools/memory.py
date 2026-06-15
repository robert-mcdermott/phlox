"""Memory tool: let the agent persist durable facts across conversations."""
from __future__ import annotations

from typing import Any

from app.agent.tools.base import Tool, ToolContext, ToolResult


class SaveMemory(Tool):
    name = "save_memory"
    description = (
        "Remember a durable fact about the user or project for future conversations "
        "(e.g. preferences, recurring context, key decisions). Use sparingly for things "
        "worth recalling later — not transient details."
    )
    category = "memory"
    default_permission = "auto"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "The fact to remember, phrased standalone"},
            "kind": {
                "type": "string",
                "enum": ["fact", "preference", "project"],
                "description": "Category of memory",
                "default": "fact",
            },
        },
        "required": ["content"],
    }

    def run(self, ctx: ToolContext, content: str = "", kind: str = "fact", **_: Any) -> ToolResult:
        from app.memory import save_memory

        if not content.strip():
            return ToolResult(content="Nothing to remember.", is_error=True)
        mem = save_memory(
            ctx.db, content, kind=kind,
            conversation_id=ctx.conversation_id, user_id=ctx.user_id,
        )
        return ToolResult(content=f"Saved to memory ({mem.kind}): {mem.content}")
