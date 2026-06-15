# MCP Integration

Phlox is an **MCP client**: it connects to Model Context Protocol servers and exposes
their tools to the model alongside the built-ins. No code is needed to add a server —
configure it in the UI (**Settings → MCP Servers**).

## How it works

`backend/app/mcp/manager.py` (`mcp_manager`) owns a single background asyncio loop. MCP's
client APIs are async context managers that must stay open between calls, so each connected
server runs a long-lived coroutine that:

1. opens the transport (`stdio_client` for command servers, `sse_client` for URLs),
2. `initialize()`s a `ClientSession`,
3. `list_tools()` and keeps the session alive until a stop event fires.

Each discovered tool is wrapped as a `_McpProxyTool` named **`mcp__<server>__<tool>`** and
registered into the shared `REGISTRY`. When the model calls it, the proxy bridges from the
synchronous harness onto the background loop (`run_coroutine_threadsafe`) and flattens the
MCP content blocks into a `ToolResult`.

Everything is defensive: if the `mcp` package is missing or a server fails to start, the
rest of the app keeps working.

## Lifecycle

- Servers are persisted in the `McpServer` table (CRUD via `/api/mcp`,
  `routers/mcp.py`).
- Enabled servers **auto-connect on startup** (`main.py::_bootstrap`).
- Connect/disconnect on demand from the UI; tools register/unregister live (the registry
  is updated by prefix `mcp__<server>__`).
- MCP tools default to the **`ask`** permission policy (they're external) — run them via
  Agent mode or set them to `auto` in the Tool Manager.

## Add a server (UI)

**Settings → MCP Servers → Add a server:**

- **stdio** (most common): give it a name, the command, and space-separated args.
  Example filesystem server:
  - name: `filesystem`
  - command: `npx`
  - args: `-y @modelcontextprotocol/server-filesystem C:\some\dir`
- **SSE**: give it a name and the server URL.

After "Add & connect", its tools appear as chips on the server card and in the Tool
Manager, and the model can call them in the next turn.

## Add a server (config / API)

`POST /api/mcp`:
```json
{ "name": "filesystem", "transport": "stdio",
  "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "."] }
```

## Troubleshooting

- **Connect fails** → check the command runs in a terminal and the `mcp` package is
  installed (`uv sync`). The connect endpoint returns the underlying error.
- **Tools don't appear** → confirm the server is "connected" (green dot) and that it
  actually advertises tools (`list_tools`).
- **Tool calls are skipped** → the tool's policy is `ask`; enable Agent mode or set it to
  `auto` in the Tool Manager.
