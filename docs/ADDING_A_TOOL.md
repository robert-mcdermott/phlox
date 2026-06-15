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
- `ctx.runner` — the `SandboxRunner` (use for executing commands/code)

### `ToolResult` fields
- `content: str` — text the model sees (keep it bounded; large outputs get truncated)
- `artifacts: list[dict]` — files to surface to the UI, each
  `{name, path, ext, size}` **relative to the workspace** (the harness adds the URL)
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

## 4. Test

```bash
# Backend running on :8000
curl -N -X POST localhost:8000/api/chat -H 'content-type: application/json' \
  -d '{"message":"use word_count on notes.txt","auto_approve":true}'
```
Watch for a `tool_call` / `tool_result` for your tool in the SSE stream.
