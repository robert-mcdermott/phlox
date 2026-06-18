# Phlox Roadmap

The path from the current working foundation to a state-of-the-art agentic chat system.
Items are tied to the code seams they touch. Check items off as they land.

**Legend:** `[ ]` todo · `[~]` in progress · `[x]` done

---

## Tier 1 — Agentic core
The things a user *feels* immediately; they make it a real agent, not "chat that calls tools."

- [x] **Human-in-the-loop approvals.** Pause the run on `ask`-policy tools, emit an
  `approval_request`, let the user Approve/Deny in the UI, and **resume** statelessly via
  a persisted `PendingApproval` (survives disconnects). _Implemented in:_
  `agent/harness.py` (pause/resume loop), `routers/chat.py` (`/api/chat/approve`),
  `models.py` (`PendingApproval`), `components/chat/ApprovalPrompt.jsx`, store
  `resolveApproval`.
- [x] **Context management / compaction.** Older turns are summarized into a synthetic
  system message once the transcript exceeds `max_context_tokens`; recent turns kept
  verbatim, split only on user-message boundaries. _Implemented in:_ `agent/context.py`,
  wired in `routers/chat.py`.
- [x] **Planning + sub-agents.** `update_todos` (plan tool, persisted per workspace) and
  `spawn_subagent` (nested ephemeral `AgentSession` with a scoped toolset in the shared
  workspace, returns a report). _Implemented in:_ `agent/tools/planning.py`,
  `agent/tools/subagent.py`, `agent/harness.py` (ephemeral + `allowed_tools`).
- [x] **Workspace checkpointing / undo.** Each workspace is a git repo; mutating tools
  auto-snapshot after running; list/restore via tools, `/api/checkpoints`, and the header
  History modal. _Implemented in:_ `workspace/checkpoints.py`,
  `agent/tools/checkpoint.py`, `routers/checkpoints.py`, `components/chat/CheckpointsModal.jsx`.
- [x] **Steering & follow-up queue.** Type while a turn is streaming to queue a follow-up
  that auto-sends when the turn finishes; Stop + the approval pause/resume cover
  interruption. _Implemented in:_ store `queued`/`resolveApproval`, `Composer.jsx`.
  (True token-level mid-run injection is out of scope for the SSE model.)

## Tier 2 — Knowledge & memory

- [x] **Real vector store (Qdrant).** Qdrant behind a `VectorStore` interface — embedded
  file-based by default, swap to a server by setting `url:` in `config.yml` (identical
  query code). SQLite stays the source of truth; `reindex_all` rebuilds the index.
  _Implemented in:_ `rag/store.py`, `rag/retrieve.py`, `rag/ingest.py`,
  `config.get_vector_store_config`.
- [x] **Hybrid search + reranking.** Dense + sparse vectors with RRF fusion + a reranker.
  Sparse vectors are dependency-free/offline; the reranker is a `Reranker` seam
  (`LexicalReranker` default, cross-encoder-ready). _Implemented in:_ `rag/store.py`
  (named dense+sparse, RRF), `rag/embed.py` (`sparse_embed`), `rag/rerank.py`,
  `rag/retrieve.py`.
- [x] **Citations.** `search_documents` returns numbered sources and instructs the model
  to cite `[n]`. (Inline highlight-on-click in the UI remains a polish item.)
- [x] **Persistent cross-conversation memory.** `memories` table; `save_memory` tool;
  relevant memories auto-retrieved into the system prompt each turn; Memory settings tab.
  _Implemented in:_ `memory.py`, `agent/tools/memory.py`, `routers/memories.py`,
  `components/settings/MemoryPanel.jsx`, wired in `routers/chat.py`.
- [x] **Multimodal input.** Image attachments on messages for vision models (OpenAI +
  Bedrock content parts), persisted + replayed; reasoning/thinking deltas surfaced; graceful
  fallback when a local model can't combine tools+vision. _Implemented in:_
  `providers/openai_provider.py` + `bedrock_provider.py`, `attachments.py`,
  `routers/attachments.py`, `Composer.jsx`, `Message.jsx`.
- [x] **Per-conversation document scoping.** Documents can be global (KB) or scoped to a
  conversation; the Composer's upload scopes to the active chat; `search_documents` filters
  to global + current-conversation docs via a Qdrant payload filter. _Implemented in:_
  `Document.conversation_id`, `rag/store.py` scope filter, `rag/retrieve.py`,
  `routers/documents.py`.
- [x] **Opt-in live web search.** `web_search` discovers current web results with
  zero-config ddgs by default, optional SearXNG via `web_search.searxng_url` /
  `SEARXNG_URL`, and a per-prompt Composer toggle keeps the tool unadvertised unless the
  user enables it for that turn. _Implemented in:_ `agent/tools/web.py`,
  `routers/chat.py`, `agent/harness.py`, `schemas.py`, `Composer.jsx`, store
  `sendMessage`.

## Tier 3 — Multi-user, isolation & operability

- [x] **Auth + multi-user + isolation.** Local username/password accounts (bcrypt) with
  JWT sessions; **Microsoft Entra ID (OIDC) seam** ready for production SSO; `user`/`admin`
  roles. Conversations, documents, memories, and settings are scoped per user; pre-auth
  data is claimed by the admin on startup. _Implemented in:_ `app/auth/*`,
  `routers/auth.py`, scoping across `routers/*` + `memory.py` + `rag/*`,
  `components/auth/LoginScreen.jsx`. Auth toggle: `auth.enabled` in `config.yml`.
- [x] **Admin panel + settings split.** Settings split into *user* (Model, Appearance,
  Documents, Memory) and *admin* (MCP Servers, Tools, Users); admin routes gated by
  `require_admin`; admin tabs/links hidden for non-admins; Users management UI.
  _Implemented in:_ `auth/deps.require_admin`, `SettingsDrawer.jsx`, `UsersPanel.jsx`.
- [x] **Container sandbox (Podman/Docker).** `ContainerRunner` behind the `SandboxRunner`
  seam, targeting the **Docker-compatible CLI** (verified with **Podman** on Windows/WSL2,
  also works with Docker; portable to macOS/Linux). Per-execution ephemeral container with
  CPU/mem/PID limits + network isolation; per-conversation workspace bind-mounted; engine
  auto-detection with graceful fallback to local. Select via `sandbox.runner: container`.
  _Implemented in:_ `sandbox/runner.py`, `config.get_sandbox_config`.
- [x] **Observability.** Per-message **token usage + cost** persisted (`Message.usage`,
  priced from `config.yml`) with a `/api/usage` aggregate and in-UI token meter; structured
  per-request logging; an optional **OpenTelemetry** tracing seam (no-op unless configured).
  _Implemented in:_ `observability.py`, `routers/usage.py`, harness usage accumulation,
  `TokenMeter.jsx`. See [OBSERVABILITY.md](OBSERVABILITY.md).
- [x] **Usage accounting / departmental chargeback.** Admin **Usage & Cost** view +
  `/api/usage/by-user` aggregating by **month × user × department × model**, with CSV export
  for finance. Backed by a durable, FK-free **`UsageLedger`** that snapshots the billable
  identity per turn and **survives user deletion**, so a departed user's department is still
  billable mid-month (the one deliberate exception to the deletion-purge guarantee; metadata
  only, never content). `department` lives on the `User` profile (set in Users admin, mapped
  from Entra claims). _Implemented in:_ `usage_ledger.py`, `models.py` (`UsageLedger`,
  `User.department`), `routers/usage.py`, `auth/{service,entra}.py`, `UsagePanel.jsx`,
  `UsersPanel.jsx`. See [OBSERVABILITY.md](OBSERVABILITY.md) + [AUTH.md](AUTH.md).
- [x] **Live admin configuration.** Admin-only **Configuration** panel to edit deployment
  config without a restart: provider profiles (API keys **write-only/masked**), model
  pricing, resilience, generation defaults, and sandbox **limits**. `config.yml` is the seed;
  a DB overlay (`AppConfig`) is merged over it by the `config.py` getters, so all consumers
  pick up changes live. Bootstrap/security-sensitive sections (`auth`, `vector_store`, OTel,
  the sandbox **runner type**) stay file-only. _Implemented in:_ `app_config.py`, `models.py`
  (`AppConfig`), `config.py` (getter overlays), `routers/admin_config.py`,
  `sandbox/runner.py` (`reset_runner`), `ConfigPanel.jsx`. See [ARCHITECTURE.md](ARCHITECTURE.md)
  §6 + [AUTH.md](AUTH.md).
- [x] **Tests + CI + evals.** pytest backend suite (unit + TestClient API + scripted-provider
  agent-loop & fallback tests), **GitHub Actions CI** (ruff + pytest + frontend build), and a
  live-model **eval harness**. _Implemented in:_ `backend/tests/`, `.github/workflows/ci.yml`,
  `backend/evals/run_evals.py`.
- [x] **Resilience.** Client **timeouts + automatic retries** (OpenAI + Bedrock, config-driven)
  and a runtime **fallback provider** that swaps in if the active model fails mid-stream.
  _Implemented in:_ `providers/*` (timeouts/retries), `agent/harness.py` (fallback loop),
  `routers/chat.py` + `config.get_resilience_config`.

## Tier 4 — UX polish

- [x] **Workspace files panel.** Header **Files** button → modal listing every file the
  agent created/edited in the conversation sandbox, with download + image preview. Also
  fixed artifact capture: any produced file (not just images/CSV) is now surfaced, and
  `write_file`/`edit_file` report their file as a downloadable artifact. _Implemented in:_
  `sandbox/runner.py` (broadened capture), `agent/tools/fs.py`, `routers/files.py`,
  `components/chat/WorkspaceFilesModal.jsx`, `Header.jsx`.
- [x] **Message edit / regenerate.** Edit a prior user message (drops it + everything
  after, then resends) and regenerate the last assistant turn. _Implemented in:_
  `routers/conversations.py` (truncate endpoint), `routers/chat.py` (`regenerate` flag),
  store `editMessage`/`regenerate`, `Message.jsx`. (Full multi-branch *tree* still TODO.)
- [x] **Search & export.** Sidebar conversation search; export any conversation to
  Markdown. _Implemented in:_ `Sidebar.jsx`, store `exportConversation`.
- [x] **Richer artifacts.** **Mermaid** diagrams + **KaTeX** math in markdown; image
  **lightbox**. _Implemented in:_ `markdown/Markdown.jsx`, `markdown/Mermaid.jsx`,
  `chat/ArtifactViewer.jsx`. (HTML/React live preview still TODO.)
- [x] **Prompt caching.** Bedrock cache points on the system prompt + tool defs
  (opt-in `prompt_cache: true` per profile; Claude models). OpenAI auto-caches.
  _Implemented in:_ `providers/bedrock_provider.py`.
- [x] **Code-splitting.** Settings drawer lazy-loaded; React/markdown/KaTeX split into
  vendor chunks; Mermaid fully lazy (off the critical path). _Implemented in:_ `App.jsx`,
  `vite.config.js`.
- [x] **Keyboard shortcuts.** New chat (Ctrl/Cmd+K), toggle sidebar (Ctrl/Cmd+\\), Esc to
  close. Command palette + voice in/out still TODO.

## API gateway (OpenAI-compatible)

Use Phlox programmatically as an authenticated LLM gateway, with the same per-user/department
cost accounting as interactive chat. See [API_GATEWAY.md](API_GATEWAY.md).

- [x] **Phase 1 — raw passthrough.** Per-user API keys (SHA-256 hashed, revocable, expiring),
  OpenAI-compatible `POST /v1/chat/completions` (streaming + buffered) and `GET /v1/models`,
  exactly one model call per request, usage recorded to the `UsageLedger`.
- [ ] **Phase 2 — agentic endpoint.** `POST /v1/agent/completions` exposing RAG + tools + MCP;
  the N model calls of one request grouped by `parent_request_id` (the ledger seam is already
  in place). Per-key budgets / rate-limiting are a candidate follow-up here.

## Tier 5 — Sensitive-data deployment (PHI)

Deferred until the app is used with real/sensitive data. **Gates any PHI use.**

- [ ] **Postgres.** Move off SQLite for concurrency/scale (SQLAlchemy already abstracts
  it; the `_ensure_columns` dev-migration shim is replaced by Alembic here).
- [ ] **Data governance / PHI.** Audit logging, secrets out of `config.yml` (vault/env),
  PII/PHI policy, provider data-retention controls, encryption at rest, BAA-compliant
  provider configuration.

---

### Status
**Tiers 1, 2, 3, and 4 are complete.** Remaining Tier 4 nice-to-haves: full
conversation-branch tree, HTML/React live artifact previews, command palette, voice.
**API gateway Phase 1 is complete; Phase 2 (`/v1/agent/completions`) is next.**
**Postgres and PHI/data-governance are Tier 5**, deferred until a sensitive-data
deployment.

See [AUTH.md](AUTH.md) (auth/SSO), [SANDBOX.md](SANDBOX.md) (container sandbox),
[OBSERVABILITY.md](OBSERVABILITY.md) (logging/usage/tracing), and
[API_GATEWAY.md](API_GATEWAY.md) (OpenAI-compatible gateway).
