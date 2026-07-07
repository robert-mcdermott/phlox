"""Tool base class + execution context + result type.

A tool advertises a JSON-Schema ``parameters`` object and implements ``run``. Tools are
synchronous (the SSE stream runs in a worker thread). The harness passes a ``ToolContext``
carrying the conversation's workspace, db session, and sandbox runner.

To add a tool, subclass ``Tool`` and register it (see ``docs/ADDING_A_TOOL.md``).
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.sandbox.runner import OutputCallback, SandboxRunner


@dataclass
class ToolContext:
    conversation_id: str
    workspace: Path
    db: Session
    runner: SandboxRunner
    user_id: str | None = None
    #: whether the *current turn* is running with ask-tier tools auto-approved. Tools that
    #: delegate to a nested agent (e.g. spawn_subagent) must respect this rather than
    #: hardcoding their own approval policy — see PermissionGate(interactive=...).
    auto_approve: bool = False
    #: set by the harness for the duration of a single tool call; a long-running tool
    #: (run_shell, execute_python/node) forwards it to the sandbox runner so partial
    #: output streams to the UI as it's produced, instead of only appearing once the
    #: whole command finishes.
    progress: OutputCallback | None = None
    #: set once per turn; a long-running tool forwards it to the sandbox runner so a
    #: user's "Stop" click (or the turn otherwise being cancelled) actually kills an
    #: in-flight subprocess instead of leaving it running server-side to completion.
    cancel_event: threading.Event | None = None


@dataclass
class ToolResult:
    """What a tool returns.

    ``content`` is the text shown to the model. ``artifacts`` are files (relative to the
    workspace) the UI can render/download. ``is_error`` flags failures.
    """

    content: str
    artifacts: list[dict] = field(default_factory=list)
    is_error: bool = False


class Tool:
    """Base class for all tools."""

    #: unique tool name (snake_case); what the model calls
    name: str = ""
    #: one-line description shown to the model
    description: str = ""
    #: JSON Schema object describing arguments
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    #: UI grouping
    category: str = "general"
    #: default permission policy: auto | ask | deny  (used to seed ToolPref)
    default_permission: str = "auto"

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:  # noqa: ARG002
        raise NotImplementedError
