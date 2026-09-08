"""MCP connections owned by one background event loop.

Each connection has one task which enters and exits its transport/session contexts.
Synchronous lifecycle requests are serialized without taking locks in the async tasks.
Disconnect waits for cleanup before the same name can reconnect, so an old task cannot
unregister a replacement session's tools. Calls are tracked and cancelled with the session.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from concurrent.futures import CancelledError, TimeoutError as FutureTimeout
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from app.agent.registry import REGISTRY
from app.agent.tools.base import Tool, ToolContext, ToolResult

logger = logging.getLogger(__name__)


@dataclass
class _Connection:
    ready: asyncio.Future
    task: asyncio.Task | None = None
    session: Any = None
    stopping: bool = False
    calls: set[asyncio.Task] = field(default_factory=set)
    tools: dict[str, Tool] = field(default_factory=dict)


class _McpProxyTool(Tool):
    category = "mcp"
    default_permission = "ask"

    def __init__(self, server: str, tool_name: str, description: str, schema: dict,
                 manager: "McpManager"):
        self.name = f"mcp__{server}__{tool_name}"
        self.description = description or f"{tool_name} (via MCP server '{server}')"
        self.parameters = schema or {"type": "object", "properties": {}}
        self._server = server
        self._tool = tool_name
        self._manager = manager

    def run(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        return self._manager.call_tool(
            self._server, self._tool, kwargs, cancel_event=ctx.cancel_event
        )


class McpManager:
    def __init__(self, *, connect_timeout: float = 30, call_timeout: float = 120,
                 cleanup_timeout: float = 5) -> None:
        self.connect_timeout = connect_timeout
        self.call_timeout = call_timeout
        self.cleanup_timeout = cleanup_timeout
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        # Access connection state only on the MCP loop.
        self._connections: dict[str, _Connection] = {}
        self._lifecycle_lock = threading.Lock()
        self._loop_lock = threading.Lock()
        self._closing = False

    def _submit(self, coro):
        with self._loop_lock:
            if self._closing:
                coro.close()
                raise RuntimeError("MCP manager is shutting down")
            if self._loop is None:
                self._loop = asyncio.new_event_loop()
                self._thread = threading.Thread(
                    target=self._loop.run_forever, daemon=True, name="mcp-loop"
                )
                self._thread.start()
            return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def connect(self, server: dict) -> dict:
        name = server["name"]
        with self._lifecycle_lock:
            try:
                # The coroutine owns its deadlines and waits for cleanup on failure.
                return self._submit(self._connect(server)).result()
            except Exception as exc:  # noqa: BLE001
                logger.warning("MCP connect failed for %s: %s", name, exc)
                return {"name": name, "connected": False,
                        "error": str(exc) or type(exc).__name__, "tools": []}

    async def _connect(self, server: dict) -> dict:
        name = server["name"]
        await self._disconnect(name)
        conn = _Connection(ready=asyncio.get_running_loop().create_future())
        self._connections[name] = conn
        conn.task = asyncio.create_task(self._serve(server, conn), name=f"mcp:{name}")
        try:
            tool_names = await asyncio.wait_for(asyncio.shield(conn.ready), self.connect_timeout)
        except TimeoutError as exc:
            await self._disconnect(name)
            raise TimeoutError(f"Timed out connecting to MCP server '{name}'") from exc
        except BaseException:
            await self._disconnect(name)
            raise
        return {"name": name, "connected": True, "tools": tool_names}

    @staticmethod
    def _build_headers(server: dict) -> dict[str, str] | None:
        headers: dict[str, str] = {}
        if server.get("auth_token"):
            headers["Authorization"] = f"Bearer {server['auth_token']}"
        explicit = server.get("headers") or {}
        if isinstance(explicit, dict):
            headers.update({str(k): str(v) for k, v in explicit.items()})
        return headers or None

    @asynccontextmanager
    async def _open_session(self, server: dict):
        """Transport seam; all context managers are entered/exited by _serve's task."""
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        transport = server.get("transport", "stdio")
        if transport == "http":
            from mcp.client.streamable_http import streamablehttp_client

            client = streamablehttp_client(server["url"], headers=self._build_headers(server))
        elif transport == "sse":
            from mcp.client.sse import sse_client

            client = sse_client(server["url"], headers=self._build_headers(server))
        else:
            client = stdio_client(StdioServerParameters(
                command=server["command"], args=server.get("args") or [],
                env=server.get("env") or None,
            ))
        async with client as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                yield session

    async def _serve(self, server: dict, conn: _Connection) -> None:
        name = server["name"]
        try:
            async with self._open_session(server) as session:
                await session.initialize()
                tools = (await session.list_tools()).tools
                conn.session = session
                names = []
                for tool in tools:
                    proxy = _McpProxyTool(
                        name, tool.name, getattr(tool, "description", "") or "",
                        getattr(tool, "inputSchema", None) or {}, self,
                    )
                    REGISTRY.register(proxy)
                    conn.tools[proxy.name] = proxy
                    names.append(proxy.name)
                conn.ready.set_result(names)
                await asyncio.Future()  # lifetime ends through cancellation in _disconnect
        except asyncio.CancelledError:
            if not conn.ready.done():
                conn.ready.cancel()
            raise
        except Exception as exc:  # noqa: BLE001
            if not conn.ready.done():
                conn.ready.set_exception(exc)
            else:
                logger.warning("MCP session %s failed: %s", name, exc)
        finally:
            conn.stopping = True
            for call in list(conn.calls):
                if not call.done() and not call.cancelling():
                    call.cancel()
            # Identity check protects against delayed teardown of a replaced connection.
            if self._connections.get(name) is conn:
                self._unregister_tools(conn)
                # Keep the record until _disconnect has also joined in-flight calls.

    @staticmethod
    def _unregister_tools(conn: _Connection) -> None:
        # Track exact tools: a server name can itself contain the '__' separator.
        for name, tool in conn.tools.items():
            if REGISTRY.get(name) is tool:
                REGISTRY.unregister(name)

    async def _disconnect(self, name: str) -> None:
        conn = self._connections.get(name)
        if conn is None:
            return
        conn.stopping = True
        self._unregister_tools(conn)
        tasks = set(conn.calls)
        if conn.task is not None:
            tasks.add(conn.task)
        for task in tasks:
            # A second cancel could interrupt the transport's finally/cleanup block.
            if not task.done() and not task.cancelling():
                task.cancel()
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=self.cleanup_timeout)
            if pending:
                # Fail closed: don't install a new connection while old resources remain.
                raise RuntimeError(f"MCP server '{name}' is still shutting down; retry later")
        if not conn.ready.done():
            conn.ready.cancel()
        elif not conn.ready.cancelled():
            conn.ready.exception()  # retrieve a failure even if its waiter was cancelled
        self._connections.pop(name, None)

    def disconnect(self, name: str) -> None:
        with self._lifecycle_lock:
            if self._loop is not None:
                self._submit(self._disconnect(name)).result()

    async def _invoke(self, server: str, tool: str, arguments: dict):
        conn = self._connections.get(server)
        if conn is None or conn.stopping or conn.session is None:
            raise RuntimeError(f"MCP server '{server}' is not connected")
        task = asyncio.current_task()
        conn.calls.add(task)
        try:
            return await conn.session.call_tool(tool, arguments)
        finally:
            conn.calls.discard(task)

    def call_tool(self, server: str, tool: str, arguments: dict,
                  *, cancel_event: threading.Event | None = None) -> ToolResult:
        if cancel_event is not None and cancel_event.is_set():
            return ToolResult(content="MCP call cancelled before dispatch. Not executed.", is_error=True)
        fut = None
        try:
            fut = self._submit(self._invoke(server, tool, arguments))
            deadline = time.monotonic() + self.call_timeout
            while True:
                cancelled = cancel_event is not None and cancel_event.is_set()
                remaining = deadline - time.monotonic()
                if cancelled or remaining <= 0:
                    fut.cancel()
                    reason = "cancelled" if cancelled else "timed out"
                    return ToolResult(
                        content=f"MCP call {reason}. Cancellation requested; the remote action's "
                        "outcome may be unknown. Check its state before retrying.", is_error=True,
                    )
                try:
                    result = fut.result(timeout=min(0.1, remaining))
                    break
                except FutureTimeout:
                    if fut.done():
                        # Completion can race the polling timeout. Retrieve the actual
                        # result (or the operation's own exception) before classifying it.
                        result = fut.result()
                        break
        except CancelledError:
            return ToolResult(
                content="MCP call cancelled/disconnected; the remote outcome may be unknown. "
                "Check its state before retrying.", is_error=True,
            )
        except Exception as exc:  # noqa: BLE001
            if fut is not None:
                fut.cancel()
            logger.warning("MCP call failed: %s/%s: %s", server, tool, exc)
            return ToolResult(content=f"MCP call error: {exc}", is_error=True)

        parts = []
        for block in getattr(result, "content", []) or []:
            text = getattr(block, "text", None)
            parts.append(text if text is not None else
                         json.dumps(getattr(block, "__dict__", str(block)), default=str))
        return ToolResult(
            content="\n".join(parts) if parts else "(no content)",
            is_error=bool(getattr(result, "isError", False)),
        )

    async def _connected_servers(self) -> list[str]:
        return [name for name, conn in self._connections.items()
                if conn.session is not None and not conn.stopping]

    def connected_servers(self) -> list[str]:
        if self._loop is None:
            return []
        return self._submit(self._connected_servers()).result()

    def close(self) -> None:
        """Drain sessions/calls, then stop the loop. May be started again next lifespan."""
        with self._lifecycle_lock:
            with self._loop_lock:
                loop, thread = self._loop, self._thread
                if loop is None:
                    return
                self._closing = True

            async def drain():
                for name in list(self._connections):
                    await self._disconnect(name)
                await loop.shutdown_asyncgens()

            try:
                asyncio.run_coroutine_threadsafe(drain(), loop).result()
            except Exception:
                # Leave the loop alive to finish cleanup; a later close can retry.
                with self._loop_lock:
                    self._closing = False
                raise
            loop.call_soon_threadsafe(loop.stop)
            thread.join()
            loop.close()
            with self._loop_lock:
                self._loop = self._thread = None
                self._closing = False


mcp_manager = McpManager()
