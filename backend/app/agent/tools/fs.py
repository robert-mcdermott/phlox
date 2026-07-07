"""Filesystem tools, scoped to the conversation workspace."""
from __future__ import annotations

import fnmatch
import re
from typing import Any

from app.agent.tools.base import Tool, ToolContext, ToolResult
from app.sandbox.runner import IGNORE_DIRS
from app.workspace.manager import resolve_in_workspace

MAX_READ_CHARS = 30_000
DEFAULT_READ_LIMIT_LINES = 2000
MAX_SEARCH_RESULTS = 200


def _skip_dir(rel_parts: tuple[str, ...]) -> bool:
    """True if a path (relative to the workspace root) is inside a vendor/build/VCS dir
    that shouldn't clutter search results (node_modules, .venv, .git, ...)."""
    return any(part in IGNORE_DIRS for part in rel_parts)


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
    description = (
        "Read a UTF-8 text file from the workspace. Returns its contents. For files "
        f"longer than {DEFAULT_READ_LIMIT_LINES} lines, only the requested window is "
        "returned — pass offset/limit to page through the rest."
    )
    category = "filesystem"
    default_permission = "auto"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path relative to the workspace root"},
            "offset": {
                "type": "integer",
                "description": "1-indexed line number to start reading from",
                "default": 1,
            },
            "limit": {
                "type": "integer",
                "description": f"Max lines to return (default {DEFAULT_READ_LIMIT_LINES})",
                "default": DEFAULT_READ_LIMIT_LINES,
            },
        },
        "required": ["path"],
    }

    def run(
        self,
        ctx: ToolContext,
        path: str = "",
        offset: int = 1,
        limit: int = DEFAULT_READ_LIMIT_LINES,
        **_: Any,
    ) -> ToolResult:
        try:
            p = resolve_in_workspace(ctx.conversation_id, path)
        except ValueError as e:
            return ToolResult(content=str(e), is_error=True)
        if not p.is_file():
            return ToolResult(content=f"No such file: {path}", is_error=True)

        lines = p.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        total = len(lines)
        start = max(int(offset or 1) - 1, 0)
        window = lines[start : start + max(int(limit or DEFAULT_READ_LIMIT_LINES), 1)]
        end = start + len(window)
        text = "".join(window)

        notes = []
        if len(text) > MAX_READ_CHARS:
            text = text[:MAX_READ_CHARS]
            notes.append(f"truncated at {MAX_READ_CHARS} chars")
        if start > 0 or end < total:
            notes.append(f"showing lines {start + 1}-{end} of {total}; use offset/limit to see more")
        if notes:
            text += f"\n... [{'; '.join(notes)}]"
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
        matches = []
        for p in root.rglob("*"):
            rel = p.relative_to(root)
            if _skip_dir(rel.parts) or not fnmatch.fnmatch(str(rel), pattern):
                continue
            matches.append(str(rel))
        matches.sort()
        if not matches:
            return ToolResult(content="(no matches)")
        shown = matches[:MAX_SEARCH_RESULTS]
        content = "\n".join(shown)
        if len(matches) > MAX_SEARCH_RESULTS:
            content += f"\n... [{len(matches) - MAX_SEARCH_RESULTS} more matches not shown; narrow the pattern]"
        return ToolResult(content=content)


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
        truncated = False
        for p in root.rglob("*"):
            rel = p.relative_to(root)
            if not p.is_file() or _skip_dir(rel.parts) or not fnmatch.fnmatch(p.name, glob):
                continue
            try:
                for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if rx.search(line):
                        out.append(f"{rel}:{i}: {line.strip()[:200]}")
                        if len(out) >= MAX_SEARCH_RESULTS:
                            truncated = True
                            break
            except OSError:
                continue
            if truncated:
                break
        if not out:
            return ToolResult(content="(no matches)")
        content = "\n".join(out)
        if truncated:
            content += f"\n... [stopped at {MAX_SEARCH_RESULTS} matches; narrow the pattern or glob to see more]"
        return ToolResult(content=content)
