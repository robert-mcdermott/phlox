"""Normalized agent events serialized to SSE.

Each event becomes a ``data: {json}\\n\\n`` SSE frame. The frontend (``api/sse.js``)
switches on ``type``:
  - status     : transient status line ("Calling read_file…")
  - thinking   : incremental reasoning text
  - token      : incremental final-answer text
  - tool_call  : a tool invocation began {id, name, arguments}
  - tool_result: a tool finished {id, name, content, is_error, artifacts}
  - artifact   : a file produced {name, path, ext, url}
  - usage      : token usage {input, output}
  - error      : fatal error {content}
  - done       : end of turn {message_id}
"""
from __future__ import annotations

import json
from typing import Any


def sse(event_type: str, **data: Any) -> str:
    payload = {"type": event_type, **data}
    return f"data: {json.dumps(payload)}\n\n"


def status(text: str) -> str:
    return sse("status", content=text)


def thinking(text: str) -> str:
    return sse("thinking", content=text)


def token(text: str) -> str:
    return sse("token", content=text)


def tool_call(call_id: str, name: str, arguments: dict) -> str:
    return sse("tool_call", id=call_id, name=name, arguments=arguments)


def tool_result(call_id: str, name: str, content: str, is_error: bool, artifacts: list) -> str:
    return sse(
        "tool_result",
        id=call_id,
        name=name,
        content=content,
        is_error=is_error,
        artifacts=artifacts,
    )


def artifact(name: str, path: str, ext: str, url: str) -> str:
    return sse("artifact", name=name, path=path, ext=ext, url=url)


def usage(info: dict) -> str:
    return sse("usage", **info)


def error(text: str) -> str:
    return sse("error", content=text)


def approval_request(pending_id: str, calls: list[dict]) -> str:
    """The run paused; these tool calls need user approval before they run."""
    return sse("approval_request", pending_id=pending_id, calls=calls)


def paused(pending_id: str) -> str:
    """Terminal event for a paused turn (instead of ``done``)."""
    return sse("paused", pending_id=pending_id)


def done(message_id: str) -> str:
    return sse("done", message_id=message_id)
