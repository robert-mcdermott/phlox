"""Code-execution tools (Python / Node), sandboxed to the workspace.

Code runs in the conversation workspace, so files it writes (e.g. ``plot.png``) are
captured as artifacts and rendered in the UI.
"""
from __future__ import annotations

from typing import Any

from app.agent.tools.base import Tool, ToolContext, ToolResult


def _format(res, label: str) -> ToolResult:
    parts = []
    if res.stdout:
        parts.append(res.stdout)
    if res.stderr:
        parts.append(f"[stderr]\n{res.stderr}")
    if not parts:
        parts.append("(no output)")
    status = " — TIMEOUT" if res.timed_out else (" — CANCELLED" if res.cancelled else "")
    parts.append(f"[{label} exit {res.exit_code}{status}, {res.duration_s}s]")
    if res.artifacts:
        parts.append("[artifacts] " + ", ".join(a["name"] for a in res.artifacts))
    return ToolResult(content="\n".join(parts), artifacts=res.artifacts, is_error=res.exit_code != 0)


class ExecutePython(Tool):
    name = "execute_python"
    description = (
        "Execute a Python 3 snippet in the workspace and return stdout/stderr. "
        "Save plots/files to the workspace to return them as artifacts (e.g. "
        "matplotlib.savefig('plot.png')). Use print() to show results."
    )
    category = "execution"
    default_permission = "ask"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Python source to run"},
            "timeout": {"type": "integer", "default": 60},
        },
        "required": ["code"],
    }

    def run(self, ctx: ToolContext, code: str = "", timeout: int = 60, **_: Any) -> ToolResult:
        res = ctx.runner.run_code(
            code, "python", ctx.workspace, timeout=timeout, on_output=ctx.progress, cancel_event=ctx.cancel_event
        )
        return _format(res, "python")


class ExecuteNode(Tool):
    name = "execute_node"
    description = "Execute a Node.js / JavaScript snippet in the workspace and return output."
    category = "execution"
    default_permission = "ask"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "JavaScript source to run"},
            "timeout": {"type": "integer", "default": 60},
        },
        "required": ["code"],
    }

    def run(self, ctx: ToolContext, code: str = "", timeout: int = 60, **_: Any) -> ToolResult:
        res = ctx.runner.run_code(
            code, "node", ctx.workspace, timeout=timeout, on_output=ctx.progress, cancel_event=ctx.cancel_event
        )
        return _format(res, "node")
