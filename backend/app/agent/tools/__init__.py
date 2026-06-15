"""Built-in tools + registration of the default tool set."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent.registry import ToolRegistry


def register_builtin_tools(registry: "ToolRegistry") -> None:
    """Register every built-in tool. Called once at startup (app.main)."""
    from app.agent.tools import (
        checkpoint,
        code,
        docs,
        fs,
        memory,
        planning,
        shell,
        subagent,
        web,
    )
    for tool in (
        fs.ReadFile(),
        fs.WriteFile(),
        fs.EditFile(),
        fs.ListDir(),
        fs.GlobSearch(),
        fs.GrepSearch(),
        shell.RunShell(),
        code.ExecutePython(),
        code.ExecuteNode(),
        docs.SearchDocuments(),
        web.WebFetch(),
        memory.SaveMemory(),
        subagent.SpawnSubagent(),
        planning.UpdateTodos(),
        checkpoint.CreateCheckpoint(),
        checkpoint.ListCheckpoints(),
        checkpoint.RestoreCheckpoint(),
    ):
        registry.register(tool)
