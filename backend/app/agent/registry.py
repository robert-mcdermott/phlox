"""Tool registry: the single surface of tools the model can call.

Built-in tools, MCP-proxied tools, and RAG tools all register here. The registry tracks
enabled/disabled state (persisted in ``ToolPref``) and produces ``ToolSpec`` objects for
providers. Lookups are by tool name.
"""
from __future__ import annotations

import logging

from app.agent.tools.base import Tool
from app.providers.base import ToolSpec

logger = logging.getLogger(__name__)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if not tool.name:
            raise ValueError(f"Tool {tool!r} has no name")
        self._tools[tool.name] = tool
        logger.debug("Registered tool %s", tool.name)

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def unregister_prefix(self, prefix: str) -> None:
        for name in [n for n in self._tools if n.startswith(prefix)]:
            self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def names(self) -> list[str]:
        return list(self._tools)

    def specs(self, enabled_names: set[str] | None = None) -> list[ToolSpec]:
        """Return ToolSpecs for advertising to a provider.

        If ``enabled_names`` is given, only those tools are included.
        """
        out: list[ToolSpec] = []
        for tool in self._tools.values():
            if enabled_names is not None and tool.name not in enabled_names:
                continue
            out.append(
                ToolSpec(name=tool.name, description=tool.description, parameters=tool.parameters)
            )
        return out


# Process-wide registry, populated at startup (app.main) and by the MCP manager.
REGISTRY = ToolRegistry()
