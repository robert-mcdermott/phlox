# Phlox Architecture

> Read this first. It explains how the whole system fits together and where to add things.
> Companion guides: [ADDING_A_TOOL.md](ADDING_A_TOOL.md) · [ADDING_A_PROVIDER.md](ADDING_A_PROVIDER.md) · [THEMING.md](THEMING.md) · [MCP.md](MCP.md)

Phlox is a feature-rich, ChatGPT-style web app. It does
chat, an agentic tool-using harness (code execution, filesystem, shell, web), document
RAG, and MCP integration — over **any** model provider (AWS Bedrock or any
OpenAI-compatible endpoint, including local models).

## 1. Big picture

```
┌─────────────────────────────┐         SSE / REST          ┌──────────────────────────────┐
│  Frontend (React + Vite)    │  ───────────────────────►   │  Backend (FastAPI)           │
│                             │                             │                              │
│  store/useStore.js (zustand)│   POST /api/chat (stream)   │  routers/chat.py             │
│  api/sse.js  api/client.js  │ ◄─────────────────────────  │      │                       │
│  components/chat/*          │                             │      ▼                       │
│  theme/ (CSS-var tokens)    │                             │  agent/harness.py  ◄── loop  │
└─────────────────────────────┘                             │      │  ToolRegistry +        │
                                                            │      │  PermissionGate        │
                                                            │      ▼                        │
                                                            │  providers/ (OpenAI|Bedrock)  │
                                                            │  agent/tools/ (fs,shell,code, │
                                                            │     docs,web)  + MCP proxies  │
                                                            │  rag/  sandbox/  workspace/   │
                                                            │  SQLite (models.py)           │
                                                            └──────────────────────────────┘
```

**Two processes.** In dev, Vite (`:5173`) proxies `/api` to FastAPI (`:8000`). In prod,
FastAPI serves the built SPA from `frontend/dist` (see `backend/app/main.py`).

## 2. The request lifecycle (most important thing to understand)

A chat turn flows through these pieces:

1. **`routers/chat.py`** receives `POST /api/chat`. It resolves/creates the
   `Conversation`, saves the user `Message`, and rebuilds the **canonical message
   history** (including past tool steps) via `_build_history`.
2. It builds an **`LLMProvider`** from the active profile (`providers/registry.py`),
   a **`PermissionGate`** (`agent/permissions.py`), and an **`AgentSession`**
   (`agent/harness.py`), then streams `AgentSession.run(history)`.
3. **`AgentSession.run`** is the agent loop. Each round it calls
   `provider.stream(messages, tools, params)` and consumes `StreamDelta`s:
   - `text` → emit a `token` SSE event (and accumulate the answer)
   - `reasoning` → emit `thinking`
   - `tool_calls` → the model wants tools this round
4. If there are tool calls, for each one it asks the `PermissionGate` (`allow|ask|deny`),
   runs the tool through the **`ToolRegistry`**, emits `tool_call` + `tool_result`
   (+ `artifact`) events, appends the result to history, and loops. If a tool's policy is
   `ask` (and the turn isn't auto-approved), the loop **pauses**: it persists a
   `PendingApproval` with the full in-flight state and emits `approval_request` + `paused`.
   The user's decision hits `POST /api/chat/approve`, which re-hydrates the state and
   **resumes** `AgentSession.resume` — stateless, so it survives disconnects.
5. When the model answers with no tool calls, the loop ends. The assistant `Message`
   (final text + structured tool steps + artifacts) is **persisted**, and a `done`
   event with the message id is emitted.
6. The frontend store (`useStore._onEvent`) assembles a **live** assistant message from
   the event stream; on `done` it re-fetches the conversation to reconcile with the
   canonical persisted state.

The **canonical message format** (provider-neutral) is documented at the top of
`providers/base.py`. Providers translate it to/from their wire formats; the harness never
deals with provider-specific shapes.

## 3. Backend module map (`backend/app/`)

| Area | Files | Responsibility |
|---|---|---|
| **Entry** | `main.py` | App, router mounting, startup wiring (DB, tools, MCP), SPA serving |
| **Config** | `config.py`, `runtime_settings.py`, `app_config.py` | `config.yml` seed (profiles/defaults) + DB-backed per-user settings + admin deployment overrides (live overlay) |
| **Persistence** | `database.py`, `models.py`, `schemas.py` | SQLite engine, ORM tables, Pydantic I/O |
| **Providers** | `providers/base.py`, `openai_provider.py`, `bedrock_provider.py`, `registry.py` | Provider abstraction + streaming + embeddings |
| **Agent** | `agent/harness.py`, `registry.py`, `permissions.py`, `events.py`, `context.py` | The resumable loop, tool registry, permission gate, SSE events, context compaction |
| **Tools** | `agent/tools/{base,fs,shell,code,docs,web,memory,planning,subagent,checkpoint}.py` | Built-in tools (file/exec/web/RAG + memory, todo planning, sub-agents, checkpoints) |
| **Memory** | `memory.py`, `routers/memories.py` | Cross-conversation memory: save + semantic retrieval into the system prompt |
| **Checkpoints** | `workspace/checkpoints.py`, `routers/checkpoints.py` | Git-backed workspace snapshots + restore (auto-snapshot after mutating tools) |
| **Rerank** | `rag/rerank.py` | Reranker seam (`LexicalReranker` default; cross-encoder-ready) |
| **Auth** | `auth/{security,service,deps,entra}.py`, `routers/auth.py` | Local login (bcrypt+JWT), admin gate, Entra ID SSO seam, user mgmt. See [AUTH.md](AUTH.md) |
| **Sandbox** | `sandbox/runner.py` | Execution isolation seam: `LocalSubprocessRunner` + `ContainerRunner` (Podman/Docker-compatible) |
| **Workspace** | `workspace/manager.py` | Per-conversation working dir + path-traversal guard |
| **RAG** | `rag/ingest.py`, `embed.py`, `retrieve.py`, `store.py` | Parse → chunk → embed → **Qdrant** vector search (`VectorStore` seam) |
| **MCP** | `mcp/manager.py` | Connect MCP servers, proxy their tools into the registry |
| **Observability** | `observability.py`, `usage_ledger.py`, `routers/usage.py` | Per-request logging, OTel seam, per-turn token/cost capture + durable chargeback ledger. See [OBSERVABILITY.md](OBSERVABILITY.md) |
| **Routers** | `routers/*.py` | `auth, chat, conversations, providers, settings, documents, mcp, tools, files, memories, checkpoints, attachments, usage, admin_config` |

### Key design decisions
- **One unified tool surface.** Built-in tools, MCP tools, and `search_documents` all
  register into the single process-wide `REGISTRY` (`agent/registry.py`). The model sees
  one list; the harness doesn't care where a tool came from.
- **Provider-agnostic harness.** Streaming differences (OpenAI tool-call deltas vs Bedrock
  `toolUse` blocks) are fully absorbed in `providers/`. Both accumulate streamed tool
  calls into complete `ToolCall`s and emit the same `StreamDelta` types.
- **Permission gate is the security seam** (`agent/permissions.py`). Each tool has a
  default policy (`auto|ask|deny`); read-only tools are `auto`, mutating/exec tools are
  `ask`. "Agent mode" in the composer sets `auto_approve`, which promotes `ask`→`allow`
  for that turn. Users override per-tool in the Tool Manager (persisted in `ToolPref`).
- **Sandbox is a swappable interface** (`sandbox/runner.py`). `LocalSubprocessRunner`
  (host, fast) and `ContainerRunner` (ephemeral Podman/Docker container with CPU/mem/PID
  limits + network isolation, workspace bind-mounted) are selected by `sandbox.runner` in
  `config.yml`. The container runner targets the Docker-compatible CLI, so Podman (incl.
  Docker-compat mode) and Docker both work across Windows/macOS/Linux.
- **Auth & multi-user.** Local username/password (bcrypt) + JWT sessions, with an Entra ID
  OIDC seam for production SSO. `get_current_user`/`require_admin` gate requests; data
  (conversations, documents, memories, settings) is scoped strictly per `user_id` —
  **admins manage accounts but cannot read other users' content**, and ownership checks
  return 404 (not 403) so existence isn't leaked. When `auth.enabled` is false the app runs
  single-user (synthetic admin). See [AUTH.md](AUTH.md).
- **Workspaces isolate file/exec tools** per conversation under
  `backend/data/workspaces/<conversation_id>/`. `resolve_in_workspace` blocks traversal.
- **Vector store is a swappable interface** (`rag/store.py`). Default `QdrantVectorStore`
  runs **embedded** (on-disk, OS-agnostic, no server); set `vector_store.url` in
  `config.yml` to point at a Qdrant server later with no query-code changes. SQLite is the
  source of truth, so `reindex_all` can rebuild the index. (Embedded Qdrant locks its dir
  to one process — don't open a second client against the same path.)
- **Long conversations are compacted** (`agent/context.py`): once the replayed transcript
  exceeds `max_context_tokens` (config default), older turns are summarized into a system
  message; recent turns stay verbatim.
- **RAG is hybrid + reranked** (`rag/`): each chunk has a named **dense** (semantic) and
  **sparse** (lexical) vector in Qdrant; retrieval queries both, fuses with RRF in Python
  (robust on embedded mode), then reranks. Sparse vectors + the default reranker are
  dependency-free/offline.
- **Cross-conversation memory** (`memory.py`): durable facts (saved by the `save_memory`
  tool or the Memory tab) are semantically retrieved each turn and appended to the system
  prompt, so the assistant "remembers" the user across chats.
- **Sub-agents** (`agent/tools/subagent.py`): `spawn_subagent` runs a nested *ephemeral*
  `AgentSession` (doesn't persist to the parent conversation) with a scoped toolset in the
  **same workspace**, and returns its report. `AgentSession` gained `ephemeral` +
  `allowed_tools` for this. Recursion is prevented by excluding `spawn_subagent` from the
  child's tools.
- **Checkpoints** (`workspace/checkpoints.py`): each workspace is a git repo; the harness
  auto-snapshots after a successful mutating tool (`MUTATING_TOOLS`), and the user can
  restore any snapshot (current state is snapshotted first, so nothing is lost).
- **Multimodal** (`attachments.py`, providers): images attach to a user message (base64
  data URLs), are persisted to `data/attachments/<msg>/` and replayed into the provider as
  image content parts for vision models. The OpenAI provider also surfaces `reasoning`
  deltas (thinking models) and retries without tools if a local model can't combine
  tools+vision.
- **Per-conversation documents** (`Document.conversation_id`): documents are global (KB) or
  scoped to one conversation; `search_documents` filters to global + the current
  conversation via a Qdrant payload filter (`rag/store.py::_scope_filter`).
- **Steering** (frontend store): a message typed while a turn streams is queued and
  auto-sent when the turn completes; Stop + approval pause/resume cover interruption.
- **Self-healing model setting** (`runtime_settings.py::_heal_model`): if the DB's active
  model isn't valid for the profile catalog (e.g. after a `config.yml` edit), it falls back
  to the profile's configured model so stale settings don't override config.

## 4. Frontend module map (`frontend/src/`)

| Area | Files | Responsibility |
|---|---|---|
| **State** | `store/useStore.js` | Zustand store: conversations, messages, settings, live streaming assembly |
| **API** | `api/client.js`, `api/sse.js` | REST client + SSE stream parser for `/api/chat` |
| **Theme** | `theme/tokens.css`, `presets.js` | CSS-variable token layer + theme catalog (FH default) |
| **Layout** | `components/layout/Header.jsx`, `Sidebar.jsx` | FH logo header, conversation list, nav |
| **Chat** | `components/chat/*` | `Message`, `ToolCallCard`, `ArtifactViewer`, `Composer`; `pages/ChatPage.jsx` |
| **Markdown** | `components/markdown/Markdown.jsx` | react-markdown + GFM + syntax highlight + copy |
| **Settings** | `components/settings/*`, `documents/*`, `mcp/*`, `tools/*` | Drawer with user tabs (Model, Appearance, Documents, Memory) + admin tabs (Users, Usage & Cost, **Configuration**, Authentication, MCP, Tools) |

The frontend renders a rich turn: collapsible **tool cards** (args + results), a
**reasoning** disclosure, inline **artifacts** (images render, files download), and
streamed markdown with highlighted, copyable code.

## 5. Data model (SQLite, `models.py`)

- `User` (role, `auth_provider`, bcrypt `password_hash`, `department` for chargeback);
  owns the rows below via `user_id`.
- `Conversation` 1—* `Message`. `Message.tool_calls` (JSON) stores each tool step
  `{id,name,arguments,content,is_error,artifacts}`; `Message.artifacts` (JSON) stores
  produced files; `Message.usage` (JSON) stores `{input,output,total,cost}`. This is what
  lets the UI re-render a full agent turn after reload.
- `Document` 1—* `DocChunk` (chunk text + JSON embedding vector).
- `Setting` (key/value), `McpServer`, `ToolPref` (enabled + permission per tool),
  `Memory` (cross-conversation facts), `PendingApproval` (paused-run state for resume).
- `UsageLedger` — append-only, **FK-free** per-turn token/cost rows with a snapshot of the
  billable identity (username/email/department). Deliberately survives user deletion for
  chargeback; see [OBSERVABILITY.md](OBSERVABILITY.md) / [AUTH.md](AUTH.md).
- `AppConfig` — admin overrides for deployment config (section key → JSON), seeded from
  `config.yml`; the overlay that powers the admin **Configuration** tab (see §6).

## 6. Configuration

Three layers, each with a clear job:

- **`backend/config.yml`** (from `config.yml.example`): the **seed / bootstrap**. Provider
  `profiles`, `defaults`, `embeddings`, plus bootstrap-or-security-sensitive sections that
  stay file-only: `auth` (incl. `jwt_secret`), `vector_store`, and
  `observability.otel`/`request_logging`. Loaded + cached in `config.py` (`@lru_cache`).
- **Deployment overrides** (DB `AppConfig` table, `app_config.py`): admin-edited, **live**
  overrides for a curated set of sections — provider `profiles`, model `pricing`,
  `resilience`, generation defaults, and sandbox **limits**. The getters in `config.py`
  (`get_profiles`, `get_observability_config`, `get_resilience_config`, `get_defaults`,
  `get_sandbox_config`) merge the overlay over the file, so every existing consumer picks up
  changes with no restart. Edited via `routers/admin_config.py` → **Settings → (Admin)
  Configuration**. Provider **secrets are write-only** (masked on read, preserved on save);
  the sandbox **runner type** is never overlay-settable (flipping isolation at runtime is
  unsafe — limits only). See [AUTH.md](AUTH.md).
- **Per-user runtime settings** (DB `Setting` table, `runtime_settings.py`): active profile,
  model, theme, system prompt, params — each user's own, changed from the Settings UI;
  seeded from the (now overlay-aware) config defaults.

> Single-process note: the overlay and `config.yml` are cached in-process and invalidated on
> write. The app is effectively single-process (embedded Qdrant locks its dir), so
> cross-process cache invalidation is out of scope; a multi-worker deployment is Tier 5.

## 7. Where to add things (quick index)

- **A new tool** → subclass `Tool`, register it. See [ADDING_A_TOOL.md](ADDING_A_TOOL.md).
- **A new provider** → implement `LLMProvider`, wire `registry.build_provider`. See
  [ADDING_A_PROVIDER.md](ADDING_A_PROVIDER.md).
- **A new theme** → add a `[data-theme]` block + a `presets.js` entry. See
  [THEMING.md](THEMING.md).
- **MCP servers** → no code; configure in the UI. Internals in [MCP.md](MCP.md).
- **A new settings tab / panel** → add a tab in `components/settings/SettingsDrawer.jsx`.
- **Harder code-exec isolation** → implement `DockerRunner` in `sandbox/runner.py`.
- **Bigger RAG corpus** → replace `rag/retrieve.search_chunks` with a vector index; keep
  the signature so `search_documents` is unaffected.

## 8. Running & verifying

See the top-level [README](../README.md) for setup (including how to run the test suite).
Quick end-to-end checks that the foundation passed (reproduce any of these):
- `GET /api/health` → `{status: ok, tools: N}` (N = number of registered tools, ~40 with
  the built-ins; MCP servers add more).
- `POST /api/chat` with "Use execute_python to compute sum(1..100)" → streams a
  `tool_call`/`tool_result` (`5050`) then `token`s then `done`.
- Upload a `.txt` document, then ask "search my documents …" → `search_documents` returns
  the matching chunk.
- Artifacts: ask Python to write `data.csv` → an `artifact` event + downloadable file at
  `/api/files/<conv>?path=data.csv`.
