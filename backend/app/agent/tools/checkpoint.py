"""Checkpoint tools: snapshot and restore the conversation workspace (git-backed)."""
from __future__ import annotations

from typing import Any

from app.agent.tools.base import Tool, ToolContext, ToolResult
from app.workspace import checkpoints


class CreateCheckpoint(Tool):
    name = "create_checkpoint"
    description = "Snapshot the current workspace so changes can be restored later."
    category = "checkpoint"
    default_permission = "auto"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {"label": {"type": "string", "description": "A short label for the snapshot"}},
    }

    def run(self, ctx: ToolContext, label: str = "checkpoint", **_: Any) -> ToolResult:
        sha = checkpoints.create_checkpoint(ctx.workspace, label or "checkpoint")
        if sha:
            return ToolResult(content=f"Created checkpoint {sha}: {label}")
        return ToolResult(content="No changes to checkpoint (workspace unchanged).")


class ListCheckpoints(Tool):
    name = "list_checkpoints"
    description = "List workspace checkpoints (snapshots) you can restore."
    category = "checkpoint"
    default_permission = "auto"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    def run(self, ctx: ToolContext, **_: Any) -> ToolResult:
        rows = checkpoints.list_checkpoints(ctx.workspace)
        if not rows:
            return ToolResult(content="No checkpoints yet.")
        return ToolResult(content="\n".join(f"{r['sha']} — {r['label']} ({r['date']})" for r in rows))


class RestoreCheckpoint(Tool):
    name = "restore_checkpoint"
    description = "Restore the workspace files to a previous checkpoint by its short sha."
    category = "checkpoint"
    default_permission = "ask"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {"sha": {"type": "string", "description": "The checkpoint short sha"}},
        "required": ["sha"],
    }

    def run(self, ctx: ToolContext, sha: str = "", **_: Any) -> ToolResult:
        if checkpoints.restore_checkpoint(ctx.workspace, sha):
            return ToolResult(content=f"Restored workspace to {sha}.")
        return ToolResult(content=f"Could not restore {sha}.", is_error=True)
