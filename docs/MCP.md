# MCP Integration

[User Guide](USER_GUIDE.md) · [Project overview](../README.md)

Phlox is an **MCP client**: it connects to Model Context Protocol servers and exposes
their tools to the model alongside the built-ins. No code is needed to add a server —
configure it in the UI (**Settings → MCP Servers**).

## How it works

`backend/app/mcp/manager.py` (`mcp_manager`) owns a single background asyncio loop. MCP's
client APIs are async context managers that must stay open between calls, so each connected
server runs a long-lived coroutine that:

1. opens the transport (`stdio_client` for command servers, `sse_client` for SSE URLs,
   or `streamablehttp_client` for streamable HTTP URLs),
2. `initialize()`s a `ClientSession`,
3. `list_tools()` and keeps the session alive until disconnect/shutdown cancels its task.

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
- Connect/disconnect on demand from the UI. Reconnect waits for the previous session's
  cleanup before registering replacements; exact tool ownership prevents an old session
  or a similarly named server from unregistering another connection's tools.
- Initialization has a 30-second timeout. Failed or expired initialization cancels its
  session task and waits for cleanup. If cleanup exceeds 5 seconds, reconnect fails with
  “still shutting down”; retry after the old transport finishes. Cleanup is not interrupted
  by repeated disconnect attempts.
- **Stop**, a call's 120-second timeout, and server disconnect request cancellation of in-flight
  local MCP calls. A remote service may already have performed an action; the error says
  its outcome may be unknown. Phlox does not automatically retry that action.
- App shutdown drains sessions/calls and stops the MCP event-loop thread.
- MCP tools default to **`ask`**, including newly connected tools without a seeded
  `ToolPref` row. Approve a pending call, enable Agent mode for the turn, or change the
  deployment-wide policy in the admin Tool Manager. Unknown tools are denied.

Offline lifecycle tests use fake sessions and a real local stdio MCP server. Remote
SSE/HTTP interoperability still needs deployment-specific checks; timeout/cancellation
does not guarantee rollback or termination inside an external service.

## Add a server (UI)

**Settings → MCP Servers → Add a server:**

- **stdio** (most common): give it a name, the command, and space-separated args.
  Example filesystem server:
  - name: `filesystem`
  - command: `npx`
  - args: `-y @modelcontextprotocol/server-filesystem C:\some\dir`
- **SSE**: give it a name and the server URL.
- **Streamable HTTP**: give it a name and the server URL.

For SSE and streamable HTTP servers, you can also provide an optional bearer token and
additional HTTP headers. The bearer token is sent as `Authorization: Bearer <token>`.
Explicit headers are merged on top, so an explicit `Authorization` header overrides the
bearer token. Stored bearer/header secrets are not returned by the MCP list API; the UI
only shows masked header values.

After "Add & connect", its tools appear as chips on the server card and in the Tool
Manager, and the model can call them in the next turn.

## Add a server (config / API)

`POST /api/mcp`:
```json
{ "name": "filesystem", "transport": "stdio",
  "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "."] }
```

Streamable HTTP with bearer auth:
```json
{ "name": "remote", "transport": "http",
  "url": "https://example.com/mcp", "auth_token": "secret-token" }
```

SSE with explicit headers:
```json
{ "name": "events", "transport": "sse",
  "url": "https://example.com/sse",
  "headers": { "X-API-Key": "secret-key" } }
```

## Troubleshooting

- **Connect fails** → check the command runs in a terminal and the `mcp` package is
  installed (`uv sync`). The connect endpoint returns the underlying error.
- **Tools don't appear** → confirm the server is "connected" (green dot) and that it
  actually advertises tools (`list_tools`).
- **Tool calls await approval** → approve/deny the pending call in chat. Agent mode
  auto-approves `ask` tools for that turn; unattended sub-agents deny unresolved `ask`
  tools. Admins can change deployment-wide policies in the Tool Manager.
