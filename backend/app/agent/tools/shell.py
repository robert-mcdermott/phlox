"""Shell execution tool (sandboxed to the workspace)."""
from __future__ import annotations

import sys
from typing import Any

from app.agent.tools.base import Tool, ToolContext, ToolResult


class RunShell(Tool):
    name = "run_shell"
    description = (
        "Run a shell command in the conversation workspace and return stdout/stderr. "
        "Use for build steps, file ops, running scripts. Times out after a limit."
    )
    category = "execution"
    default_permission = "ask"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Command line to execute"},
            "timeout": {"type": "integer", "description": "Seconds before timeout", "default": 60},
        },
        "required": ["command"],
    }

    def run(self, ctx: ToolContext, command: str = "", timeout: int = 60, **_: Any) -> ToolResult:
        if not command.strip():
            return ToolResult(content="Empty command", is_error=True)
        # On Windows use the shell; elsewhere split safely.
        if sys.platform == "win32":
            argv = ["cmd", "/c", command]
        else:
            argv = ["/bin/sh", "-c", command]
        res = ctx.runner.run_command(
            argv, ctx.workspace, timeout=timeout, on_output=ctx.progress, cancel_event=ctx.cancel_event
        )
        parts = []
        if res.stdout:
            parts.append(res.stdout)
        if res.stderr:
            parts.append(f"[stderr]\n{res.stderr}")
        status = " — TIMEOUT" if res.timed_out else (" — CANCELLED" if res.cancelled else "")
        parts.append(f"[exit {res.exit_code}{status}]")
        return ToolResult(
            content="\n".join(parts),
            artifacts=res.artifacts,
            is_error=res.exit_code != 0,
        )
