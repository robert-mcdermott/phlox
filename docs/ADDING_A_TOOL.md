# Adding a Tool

Tools are how the agent acts. A tool advertises a JSON-Schema and implements `run`.
Built-in, MCP, and RAG tools all share the same `Tool` base and the same registry, so the
model sees one unified surface.

## 1. Write the tool

Create or extend a module in `backend/app/agent/tools/`. Subclass `Tool`:

```python
# backend/app/agent/tools/example.py
from typing import Any
from app.agent.tools.base import Tool, ToolContext, ToolResult


class WordCount(Tool):
    name = "word_count"                       # snake_case; what the model calls
    description = "Count words in a workspace text file."
    category = "filesystem"                   # UI grouping
    default_permission = "auto"               # auto | ask | deny  (seeds ToolPref)
    parameters: dict[str, Any] = {            # JSON Schema for arguments
        "type": "object",
        "properties": {"path": {"type": "string", "description": "File path in the workspace"}},
        "required": ["path"],
    }

    def run(self, ctx: ToolContext, path: str = "", **_: Any) -> ToolResult:
        from app.workspace.manager import resolve_in_workspace
        p = resolve_in_workspace(ctx.conversation_id, path)
        if not p.is_file():
            return ToolResult(content=f"No such file: {path}", is_error=True)
        n = len(p.read_text(encoding="utf-8", errors="replace").split())
        return ToolResult(content=f"{n} words in {path}")
```

### `ToolContext` gives you
- `ctx.conversation_id` — the conversation
- `ctx.workspace` — `Path` to the per-conversation working dir (sandbox root)
- `ctx.db` — a SQLAlchemy `Session`
- `ctx.user_id` — the owning user, or `None` when auth is disabled
- `ctx.runner` — the `SandboxRunner` (use for executing commands/code)
- `ctx.auto_approve` — whether the current turn has "Agent mode" on. If your tool
  delegates to a nested `AgentSession` (like `spawn_subagent`), pass this through rather
  than hardcoding a policy — don't let a tool grant itself permissions the user didn't
  give the turn.
- `ctx.progress` — `Callable[[str], None] | None`. For a long-running tool, call it with
  each chunk of output as you produce it and it streams to the UI live as a
  `tool_progress` event instead of only appearing when `run` returns. `run_shell` /
  `execute_python` / `execute_node` forward it to `ctx.runner.run_command`/`run_code` as
  `on_output=ctx.progress` — follow that pattern for anything wrapping a subprocess.
- `ctx.cancel_event` — `threading.Event | None`, set when the turn is cancelled (timeout
  or the user's Stop). A long-running tool should poll it and stop promptly; pass it
  through to `ctx.runner.run_command`/`run_code` as `cancel_event=ctx.cancel_event` if
  you're wrapping a subprocess — the local/container runners already kill the whole
  process tree when it's set, not just the immediate child.

### `ToolResult` fields
- `content: str` — text the model sees (keep it bounded; large outputs get truncated)
- `artifacts: list[dict]` — files to surface to the UI, each
  `{name, path, ext, size}` **relative to the workspace** (the harness adds the URL).
  `.html`/`.htm` and `.md`/`.markdown` artifacts auto-open in the live **artifact canvas**
  (`frontend/src/utils/canvas.js::canvasKind`); other non-binary extensions get a
  plain-text canvas preview; images and known binary formats stay download-only.
- `is_error: bool`

Rules of thumb: **stay inside the workspace** (use `resolve_in_workspace`); never block
indefinitely (pass timeouts to `ctx.runner`); return concise, model-readable text.

## 2. Register it

Add it to the default set in `backend/app/agent/tools/__init__.py`:

```python
def register_builtin_tools(registry):
    from app.agent.tools import code, docs, fs, shell, web, example  # add module
    for tool in (
        ...,
        example.WordCount(),   # add instance
    ):
        registry.register(tool)
```

That's it. On next startup the tool is registered, a `ToolPref` row is seeded with its
`default_permission`, it appears in the **Tools** settings tab, and the model can call it.

## 3. Permissions

- `auto` tools run without prompting.
- `ask` tools only run when the turn has `auto_approve` (the composer's **Agent mode**) or
  the user sets them to `auto` in the Tool Manager; otherwise they're skipped with a note
  back to the model.
- `deny` tools never run.

Mutating or executing tools should default to `ask`. Read-only tools can be `auto`.

If your tool is only ever driven by an unattended nested session (there's no human to
answer an approval pause — `spawn_subagent` is the only current example), build its
`PermissionGate` with `interactive=False`. That makes `ask` resolve to `deny` instead of
either hanging on an unanswerable pause or silently becoming `allow` — an unattended
tool call must never grant itself a permission the surrounding turn didn't have.

Some built-in tools also need per-turn user intent, not just a global tool preference.
For those, keep the tool registered normally, but pass an `allowed_tools` set into
`AgentSession` from `routers/chat.py` and omit the tool unless the request opts in. The
`web_search` tool is the reference pattern: it appears in the Tool Manager, but the model
only sees it when the Composer sends `web_search: true` for that prompt.

## 4. Test

```bash
# Backend running on :8000
curl -N -X POST localhost:8000/api/chat -H 'content-type: application/json' \
  -d '{"message":"use word_count on notes.txt","auto_approve":true}'
```
Watch for a `tool_call` / `tool_result` for your tool in the SSE stream.
