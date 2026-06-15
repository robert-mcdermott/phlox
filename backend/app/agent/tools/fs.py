"""Filesystem tools, scoped to the conversation workspace."""
from __future__ import annotations

import fnmatch
import re
from typing import Any

from app.agent.tools.base import Tool, ToolContext, ToolResult
from app.workspace.manager import resolve_in_workspace

MAX_READ_CHARS = 30_000


def _rel(ctx: ToolContext, path) -> str:
    try:
        return str(path.relative_to(ctx.workspace))
    except ValueError:
        return str(path)


def _artifact(ctx: ToolContext, p) -> list[dict]:
    """Build an artifact entry so a written file is viewable/downloadable in the UI."""
    try:
        rel = str(p.relative_to(ctx.workspace))
    except ValueError:
        return []
    return [{"name": rel, "path": rel, "ext": p.suffix.lower(), "size": p.stat().st_size}]


class ReadFile(Tool):
    name = "read_file"
    description = "Read a UTF-8 text file from the workspace. Returns its contents."
    category = "filesystem"
    default_permission = "auto"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Path relative to the workspace root"}},
        "required": ["path"],
    }

    def run(self, ctx: ToolContext, path: str = "", **_: Any) -> ToolResult:
        try:
            p = resolve_in_workspace(ctx.conversation_id, path)
        except ValueError as e:
            return ToolResult(content=str(e), is_error=True)
        if not p.is_file():
            return ToolResult(content=f"No such file: {path}", is_error=True)
        text = p.read_text(encoding="utf-8", errors="replace")
        if len(text) > MAX_READ_CHARS:
            text = text[:MAX_READ_CHARS] + "\n... [truncated]"
        return ToolResult(content=text)


class WriteFile(Tool):
    name = "write_file"
    description = "Create or overwrite a text file in the workspace."
    category = "filesystem"
    default_permission = "ask"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path relative to the workspace root"},
            "content": {"type": "string", "description": "Full file content to write"},
        },
        "required": ["path", "content"],
    }

    def run(self, ctx: ToolContext, path: str = "", content: str = "", **_: Any) -> ToolResult:
        try:
            p = resolve_in_workspace(ctx.conversation_id, path)
        except ValueError as e:
            return ToolResult(content=str(e), is_error=True)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return ToolResult(content=f"Wrote {len(content)} chars to {path}", artifacts=_artifact(ctx, p))


class EditFile(Tool):
    name = "edit_file"
    description = (
        "Replace the first occurrence of an exact string in a workspace file. "
        "Use unique old_string. Set replace_all to change every occurrence."
    )
    category = "filesystem"
    default_permission = "ask"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_string": {"type": "string"},
            "new_string": {"type": "string"},
            "replace_all": {"type": "boolean", "default": False},
        },
        "required": ["path", "old_string", "new_string"],
    }

    def run(
        self,
        ctx: ToolContext,
        path: str = "",
        old_string: str = "",
        new_string: str = "",
        replace_all: bool = False,
        **_: Any,
    ) -> ToolResult:
        try:
            p = resolve_in_workspace(ctx.conversation_id, path)
        except ValueError as e:
            return ToolResult(content=str(e), is_error=True)
        if not p.is_file():
            return ToolResult(content=f"No such file: {path}", is_error=True)
        text = p.read_text(encoding="utf-8")
        if old_string not in text:
            return ToolResult(content="old_string not found in file", is_error=True)
        count = text.count(old_string)
        if count > 1 and not replace_all:
            return ToolResult(
                content=f"old_string is not unique ({count} matches). Use replace_all or a longer string.",
                is_error=True,
            )
        text = text.replace(old_string, new_string) if replace_all else text.replace(old_string, new_string, 1)
        p.write_text(text, encoding="utf-8")
        return ToolResult(
            content=f"Edited {path} ({'all' if replace_all else 1} replacement(s))",
            artifacts=_artifact(ctx, p),
        )


class ListDir(Tool):
    name = "list_dir"
    description = "List files and directories at a path within the workspace."
    category = "filesystem"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {"path": {"type": "string", "default": "."}},
    }

    def run(self, ctx: ToolContext, path: str = ".", **_: Any) -> ToolResult:
        try:
            p = resolve_in_workspace(ctx.conversation_id, path)
        except ValueError as e:
            return ToolResult(content=str(e), is_error=True)
        if not p.exists():
            return ToolResult(content=f"No such path: {path}", is_error=True)
        if p.is_file():
            return ToolResult(content=_rel(ctx, p))
        entries = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
        lines = [f"{'📁' if e.is_dir() else '📄'} {e.name}" for e in entries]
        return ToolResult(content="\n".join(lines) if lines else "(empty)")


class GlobSearch(Tool):
    name = "glob_search"
    description = "Find files in the workspace matching a glob pattern (e.g. **/*.py)."
    category = "filesystem"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {"pattern": {"type": "string"}},
        "required": ["pattern"],
    }

    def run(self, ctx: ToolContext, pattern: str = "*", **_: Any) -> ToolResult:
        root = resolve_in_workspace(ctx.conversation_id, ".")
        matches = [str(p.relative_to(root)) for p in root.rglob("*") if fnmatch.fnmatch(str(p.relative_to(root)), pattern)]
        matches.sort()
        return ToolResult(content="\n".join(matches[:200]) if matches else "(no matches)")


class GrepSearch(Tool):
    name = "grep_search"
    description = "Search workspace file contents for a regex pattern. Returns matching lines."
    category = "filesystem"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "glob": {"type": "string", "description": "Optional file glob filter", "default": "*"},
        },
        "required": ["pattern"],
    }

    def run(self, ctx: ToolContext, pattern: str = "", glob: str = "*", **_: Any) -> ToolResult:
        try:
            rx = re.compile(pattern)
        except re.error as e:
            return ToolResult(content=f"Bad regex: {e}", is_error=True)
        root = resolve_in_workspace(ctx.conversation_id, ".")
        out: list[str] = []
        for p in root.rglob("*"):
            if not p.is_file() or not fnmatch.fnmatch(p.name, glob):
                continue
            try:
                for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if rx.search(line):
                        out.append(f"{p.relative_to(root)}:{i}: {line.strip()[:200]}")
                        if len(out) >= 200:
                            break
            except OSError:
                continue
            if len(out) >= 200:
                break
        return ToolResult(content="\n".join(out) if out else "(no matches)")
