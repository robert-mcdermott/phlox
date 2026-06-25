"""Connect to MCP servers and expose their tools through the shared ToolRegistry.

Design: a single background asyncio loop hosts long-lived client sessions (MCP's
client APIs are async context managers that must stay open between calls). Each
connected server's tools are wrapped as ``Tool`` instances named
``mcp__<server>__<tool>`` and registered into ``REGISTRY``. Tool calls bridge from the
synchronous harness onto the background loop via ``run_coroutine_threadsafe``.

Everything is defensive: if the ``mcp`` package is missing or a server fails to start,
the rest of the app keeps working.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any

from app.agent.registry import REGISTRY
from app.agent.tools.base import Tool, ToolContext, ToolResult

logger = logging.getLogger(__name__)


class _McpProxyTool(Tool):
    """A registry tool that forwards calls to an MCP server."""

    category = "mcp"
    default_permission = "ask"

    def __init__(self, server: str, tool_name: str, description: str, schema: dict, manager: "McpManager"):
        self.name = f"mcp__{server}__{tool_name}"
        self.description = description or f"{tool_name} (via MCP server '{server}')"
        self.parameters = schema or {"type": "object", "properties": {}}
        self._server = server
        self._tool = tool_name
        self._manager = manager

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:  # noqa: ARG002
        return self._manager.call_tool(self._server, self._tool, kwargs)


class McpManager:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._sessions: dict[str, Any] = {}
        self._stoppers: dict[str, asyncio.Event] = {}
        self._lock = threading.Lock()

    # -- background loop ----------------------------------------------------
    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop and self._loop.is_running():
            return self._loop
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True, name="mcp-loop")
        self._thread.start()
        return self._loop

    def _submit(self, coro):
        loop = self._ensure_loop()
        return asyncio.run_coroutine_threadsafe(coro, loop)

    # -- connection lifecycle ----------------------------------------------
    def connect(self, server: dict) -> dict:
        """Connect a server config dict and register its tools. Returns a status dict."""
        name = server["name"]
        with self._lock:
            if name in self._sessions:
                self.disconnect(name)
            try:
                fut = self._submit(self._serve(server))
                tools = fut.result(timeout=30)
            except Exception as e:  # noqa: BLE001
                logger.exception("MCP connect failed for %s", name)
                return {"name": name, "connected": False, "error": str(e), "tools": []}

            tool_names = []
            for t in tools:
                schema = getattr(t, "inputSchema", None) or {"type": "object", "properties": {}}
                proxy = _McpProxyTool(name, t.name, getattr(t, "description", "") or "", schema, self)
                REGISTRY.register(proxy)
                tool_names.append(proxy.name)
            return {"name": name, "connected": True, "tools": tool_names}
            
    @staticmethod
    def _build_headers(server: dict) -> dict[str, str] | None:
        """Build HTTP headers for network transports (sse/http) from optional auth config.

        ``auth_token`` becomes ``Authorization: Bearer <token>``; explicit ``headers`` are
        merged on top (and override it). Returns ``None`` when nothing is configured, so the
        transport is invoked exactly as before.
        """
        headers: dict[str, str] = {}
        token = server.get("auth_token")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        explicit = server.get("headers") or {}
        if isinstance(explicit, dict):
            headers.update({str(k): str(v) for k, v in explicit.items()})
        return headers or None
    async def _serve(self, server: dict):
        """Open and hold a session open until its stop event is set; return tool list."""
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        name = server["name"]
        stop = asyncio.Event()
        self._stoppers[name] = stop
        ready: asyncio.Future = asyncio.get_event_loop().create_future()

        async def _runner():
            try:
                transport = server.get("transport", "stdio")
                if transport == "http":
                    from mcp.client.streamable_http import streamablehttp_client

                    headers = self._build_headers(server)
                    # streamablehttp_client yields a THIRD value (a get_session_id
                    # callback) that the sse/stdio transports do not — we don't use it.
                    async with streamablehttp_client(server["url"], headers=headers) as (read, write, _):
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            self._sessions[name] = session
                            tools = (await session.list_tools()).tools
                            ready.set_result(tools)
                            await stop.wait()
                elif transport == "sse":
                    from mcp.client.sse import sse_client

                    headers = self._build_headers(server)
                    async with sse_client(server["url"], headers=headers) as (read, write):
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            self._sessions[name] = session
                            tools = (await session.list_tools()).tools
                            ready.set_result(tools)
                            await stop.wait()
                else:
                    params = StdioServerParameters(
                        command=server["command"],
                        args=server.get("args") or [],
                        env=server.get("env") or None,
                    )
                    async with stdio_client(params) as (read, write):
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            self._sessions[name] = session
                            tools = (await session.list_tools()).tools
                            ready.set_result(tools)
                            await stop.wait()
            except Exception as e:  # noqa: BLE001
                if not ready.done():
                    ready.set_exception(e)
            finally:
                self._sessions.pop(name, None)

        asyncio.create_task(_runner())
        return await ready

    def disconnect(self, name: str) -> None:
        with self._lock:
            stop = self._stoppers.pop(name, None)
            if stop is not None and self._loop:
                self._loop.call_soon_threadsafe(stop.set)
            self._sessions.pop(name, None)
            REGISTRY.unregister_prefix(f"mcp__{name}__")

    # -- tool calls ---------------------------------------------------------
    def call_tool(self, server: str, tool: str, arguments: dict) -> ToolResult:
        session = self._sessions.get(server)
        if session is None:
            return ToolResult(content=f"MCP server '{server}' is not connected.", is_error=True)
        try:
            fut = self._submit(session.call_tool(tool, arguments))
            result = fut.result(timeout=120)
        except Exception as e:  # noqa: BLE001
            logger.exception("MCP call failed: %s/%s", server, tool)
            return ToolResult(content=f"MCP call error: {e}", is_error=True)

        # Flatten MCP content blocks to text.
        parts: list[str] = []
        for block in getattr(result, "content", []) or []:
            text = getattr(block, "text", None)
            if text is not None:
                parts.append(text)
            else:
                parts.append(json.dumps(getattr(block, "__dict__", str(block)), default=str))
        is_error = bool(getattr(result, "isError", False))
        return ToolResult(content="\n".join(parts) if parts else "(no content)", is_error=is_error)

    def connected_servers(self) -> list[str]:
        return list(self._sessions)


mcp_manager = McpManager()
