"""Planning tool: a todo list the agent maintains while working a multi-step task.

The list is persisted per-conversation (in ``.phlox/todos.json`` in the workspace) so
it survives across turns. The tool returns the rendered checklist, which the UI shows in
the tool card — giving the user visibility into the agent's plan and progress.
"""
from __future__ import annotations

import json
from typing import Any

from app.agent.tools.base import Tool, ToolContext, ToolResult

_STATUS_ICON = {"pending": "☐", "in_progress": "▶", "completed": "☑"}


class UpdateTodos(Tool):
    name = "update_todos"
    description = (
        "Create or update your task plan for multi-step work. Pass the full list each time. "
        "Mark exactly one item 'in_progress' while working it, 'completed' when done. Use "
        "this to plan before acting and to track progress on complex tasks."
    )
    category = "planning"
    default_permission = "auto"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "description": "The full ordered task list",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                    },
                    "required": ["content", "status"],
                },
            }
        },
        "required": ["todos"],
    }

    def run(self, ctx: ToolContext, todos: list | None = None, **_: Any) -> ToolResult:
        todos = todos or []
        state_dir = ctx.workspace / ".phlox"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "todos.json").write_text(json.dumps(todos, indent=2), encoding="utf-8")

        if not todos:
            return ToolResult(content="(empty plan)")
        lines = [f"{_STATUS_ICON.get(t.get('status', 'pending'), '☐')} {t.get('content', '')}" for t in todos]
        done = sum(1 for t in todos if t.get("status") == "completed")
        return ToolResult(content=f"Plan ({done}/{len(todos)} done):\n" + "\n".join(lines))
