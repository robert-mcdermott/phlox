# Phlox codebase and product review

**Review date:** 2026-09-07 (America/Los_Angeles). **Baseline:** `e07ddc7d`.
**Purpose:** inform the [active improvement roadmap](ROADMAP.md), not certify production
security or benchmark Phlox against commercial assistants.

> This is the **pre-implementation baseline**. [Wave 1](IMPLEMENTATION_WAVES.md) subsequently
> fixes the MCP/delegation paths in R3–R4 and permission defaults from R14, with 215 passing
> tests. Wave 2 addresses approval continuity in R1–R2 and begins browser isolation
> coverage, reaching 246 backend tests and 4 browser scenarios. Findings below retain the original evidence; consult the wave log for current
> implementation status and remaining limitations.

## Assessment

Phlox has a substantial, coherent foundation. The provider abstraction, unified tool
registry, resumable approvals, sandbox interface, ownership checks, and configuration
overlay are useful extension seams. It already has more capability than the old roadmap's
summary suggests, including skills, optional Postgres, AgentCore execution, hardened
bootstrap authentication, and production isolation checks.

The main gap is the distance between **a feature existing** and **a complete user journey
being dependable**. A document search tool is not yet an inspectable research experience;
a Markdown preview is not yet a document workspace; an approval snapshot is not yet a
durable background task. The next investment should connect these features and strengthen
their contracts, with source-backed research as the first major product improvement.

Keep the current stack. Introduce explicit run, source, project, artifact-version, and
model-call records incrementally. Do not replace the harness or database merely to adopt
a different agent framework.

## Review scope and verification

Inspected architecture/roadmap and the focused tool, provider, auth, sandbox, deployment,
RAG, skills, MCP, guardrails, budgets, observability, API gateway, and theme documentation.
Traced chat creation, history assembly, tool execution, approval resume, child execution,
usage finalization, retrieval/ingestion, frontend event handling, canvas, configuration,
and persistence. Reviewed tests, the live eval harness, dependency manifests, and CI.

This was a targeted architectural and implementation review, not a line-by-line audit of
every module. Frontend conclusions are from code inspection; no interactive browser audit
or accessibility/performance measurement was performed. No real providers, personal MCP
servers, cloud accounts, or private app data were used. Existing untracked local files
were left untouched and are not the evidence baseline.

| Check | Result |
|---|---|
| `uv run --frozen --extra dev ruff check app tests` in `backend/` | Passed |
| `uv run --frozen --extra dev pytest` in `backend/` | **179 passed**, 9 warnings, 11.90 s |
| `npm run build` in `frontend/` | Passed; Vite reported some chunks over 500 kB, including lazy diagram dependencies |
| Isolated MCP reconnect fixture | Existing-session reconnect remained blocked on its own lock; no transport/network started |
| Isolated context-budget fixture | An 80,000-character user turn remained at 20,001 estimated tokens despite a 12,000-token budget |

Backend commands used a temporary `UV_CACHE_DIR`. Pytest config isolates its data under
a temporary directory. The two extra fixtures used temporary configuration/data and did
not alter application code. The MCP fixture put an inert object in `_sessions`, called
`connect()` in a daemon thread, and observed the blocked thread/held lock after 250 ms;
the reviewed call graph identifies the nested acquisition. The process exited afterwards.

Warnings were the Starlette TestClient/httpx deprecation and Pydantic class-based config
deprecations. No dependency vulnerability assessment was performed. A bundle warning is
not a measured first-load regression; Mermaid is already lazy-loaded.

The default TestClient fixture skips lifespan (vector-store/MCP startup), and the main
fixture disables auth, while dedicated tests exercise auth and security paths. The suite
does not demonstrate deployed Postgres, actual containers, live provider interoperability,
or end-to-end browser recovery. Docker image-contract CI exists, but was not run locally.

## Existing capabilities worth building on

| Area | Evidence | Assessment |
|---|---|---|
| Agent core | [harness](../backend/app/agent/harness.py), [permissions](../backend/app/agent/permissions.py), [registry](../backend/app/agent/registry.py) | Working tool loop, approval state, output progress, fallback, checkpoints, and child sessions; improve lifecycle/accounting instead of replacing it |
| Provider support | [base contract](../backend/app/providers/base.py), [OpenAI-compatible adapter](../backend/app/providers/openai_provider.py), [Bedrock adapter](../backend/app/providers/bedrock_provider.py) | Good separation; capability negotiation is largely a tools boolean plus adapter-specific behavior |
| Knowledge | [RAG modules](../backend/app/rag/), [memory](../backend/app/memory.py), [document tools](../backend/app/agent/tools/docs.py) | Hybrid retrieval and scoped memory exist; extraction, source identity, degraded-mode semantics, and quality evaluation need work |
| Reuse | [skills](../backend/app/skills.py), [skill routes](../backend/app/routers/skills.py), [assistants](../backend/app/routers/assistants.py) | SKILL.md import/export, slash invocation, progressive disclosure, personal/private skills, admin assistants/KBs already exist |
| Isolation/operations | [sandbox](../backend/app/sandbox/runner.py), [auth](../backend/app/auth/), [config](../backend/app/config.py), [CI](../.github/workflows/ci.yml) | Fail-closed configured execution, bootstrap controls, strict content ownership, live config, and image contract are substantial strengths |
| User experience | [store](../frontend/src/store/useStore.js), [canvas](../frontend/src/components/canvas/CanvasPanel.jsx), [composer](../frontend/src/components/chat/Composer.jsx) | Rich chat surface already present; recovery, context organization, editing, accessibility, and testing are the larger opportunities |

## Findings and implications

“Code-confirmed” means the behavior/path is visible in the implementation; it does not
imply a live exploit or exhaustive runtime verification. “Reproduced” denotes the isolated
checks above. Priorities below are delivery priorities, not CVSS vulnerability scores.

### R1 — Run lifetime and approval recovery depend on transient UI state

**High priority; code-confirmed.** [Chat router](../backend/app/routers/chat.py)
`_watch_disconnect` sets cancellation when the SSE client disconnects. The
[store](../frontend/src/store/useStore.js) also calls `stopStreaming()` when selecting
another chat or creating a new one. Navigation therefore participates in stopping work.

`PendingApproval` persists server state, but conversation detail does not return it and
`selectConversation` clears `live`; the approval UI relies on `live.pendingApproval`.
The saved snapshot is insufficient for users to rediscover an approval after refresh.
Ordinary in-progress tokens/tool state have no durable event log. A stream ending is
handled through the same client finalization path, without a durable terminal run status.

**Action:** M1.2 durable runs/events with explicit cancellation and replay; M1.1 pending
approval recovery as an early visible fix. Test refresh, navigation, multiple tabs, and
restart rather than assuming SSE transport itself prevents resumability.

### R2 — Resume and model-call accounting are incomplete

**High priority; code-confirmed.** In the [harness](../backend/app/agent/harness.py),
`_pause` saves messages/tools/artifacts/settings but omits `turn_usage` and consumed round
count. A new `AgentSession` initializes usage to zero and `_loop` starts its round budget
again. `_finalize` exits early for ephemeral children, so their usage is not recorded there.
[Compaction](../backend/app/agent/context.py) consumes only text deltas and discards usage.
Fallback aggregates usage and prices the result with the final provider's model.

The [approval route](../backend/app/routers/chat.py) reads and deletes the pending row
before execution, without an atomic claim protecting simultaneous approvals. It does not
repeat the initial budget gate. Explicit saved decisions can take precedence over current
tool policy in `_process_calls`. These paths need focused concurrency/policy regressions.

**Action:** F03/F04, then durable transitions in F07. Persist cumulative consumption,
recheck current policy, record each model call with stable IDs, and price the model actually
used. Treat unknown consumption and uncertain interrupted actions explicitly. Monthly
budget enforcement is currently a next-turn guard, not a reservation or hard spend ceiling.

### R3 — Children do not inherit the parent's full execution context

**High priority; code-confirmed.** [SpawnSubagent.run](../backend/app/agent/tools/subagent.py)
calls `get_settings(db)` without `ctx.user_id`, then resolves its provider from those
unnamespaced/default settings. Its `allowed` set comes from `SUBAGENT_TOOLS` intersected
with registered names, not the parent's per-turn tool set. User and assistant IDs and
cancellation propagate via the conversation/context; approval mode is inherited correctly,
but that does not carry all source/model constraints.

The harness launches one thread per requested child without a configured fan-out bound.
Children share the workspace. [Checkpoint locks](../backend/app/workspace/checkpoints.py)
serialize Git operations, not arbitrary file mutations or logical task dependencies.

**Action:** F02 explicit inherited context and bounded child execution. Test distinct user
settings and restricted document/tool scopes; serialize conflicting writes or isolate
child workspaces with explicit merge. Do not advertise reliable parallel autonomy yet.

### R4 — MCP reconnect has a lock deadlock; cancellation is incomplete

**High priority; reproduced reconnect path.** In
[McpManager](../backend/app/mcp/manager.py), `connect()` takes `threading.Lock`, then calls
`disconnect()` for an existing session; `disconnect()` takes that same lock. The isolated
fixture blocks before reaching any network transport.

Separately, `call_tool()` waits on a future with a timeout, but the exception path does not
cancel the submitted operation. The proxy ignores `ToolContext.cancel_event`. A caller
timeout does not establish that a remote action stopped. Connections/credentials are
deployment-wide, and content blocks are flattened to text.

**Action:** F01 locking and lifecycle regressions; M4 scoped connections and rich results.
Track an unknown external outcome instead of automatically retrying a mutating call.

### R5 — Context compaction is a heuristic, not an enforced budget

**High priority; oversized short-history behavior reproduced.**
[context.py](../backend/app/agent/context.py) estimates characters/4, preserves four recent
user turns, and returns unchanged if there are too few turns. It does not account for
image costs or tool schemas. Chat compacts once before running; the harness does not
repeat a fit check as tool output accumulates. Summaries are recomputed from stored history
on later turns rather than saved as versioned context state.

**Action:** F04 input/output budgeting and explicit oversized-input handling; M3 structured
context and cached/versioned summaries. Preserve tool-call/result boundaries and original
source records. Do not solve overflow by silently dropping important recent instructions.

### R6 — Embedding fallback can change the meaning of the index

**High priority; code-confirmed, outage behavior not live-tested.**
[embed_texts](../backend/app/rag/embed.py) falls back to a 512-dimensional hash vector if a
configured remote embedder fails. [sync_index](../backend/app/rag/maintenance.py) detects
changes by dimension only, probes through that fallback, and may re-embed/rebuild at startup.
There is no stored embedding-model identity. Different models with the same dimension
can therefore mix incompatible spaces undetected; outages can also change the dimension.

**Action:** F09 embedding/parser/index identity, controlled rebuilds, last-good-index
retention, and explicit lexical-only degradation. Keep the simple offline option, but label
its retrieval quality honestly and avoid automatic mixing of vector spaces.

### R7 — Citations lack stable, inspectable evidence records

**High product priority; code-confirmed.**
[SearchDocuments](../backend/app/agent/tools/docs.py) numbers each result batch starting at
1 and emits filenames/chunk ordinals in text. Direct references use separate `[D1]` labels
in the [chat router](../backend/app/routers/chat.py). The
[Markdown renderer](../frontend/src/components/markdown/Markdown.jsx) has ordinary links
but no source resolver. There is no shared citation registry across multiple calls.

**Action:** F08/M2 stable source IDs, typed citation metadata, exact passage navigation,
claim-support evaluation, and export fidelity. Preserve the difference between a source
being present and that source actually supporting the answer.

### R8 — Ingestion is basic and not restart-resilient

**Medium/high priority; code-confirmed.** [Ingestion](../backend/app/rag/ingest.py) flattens
PDF pages, extracts DOCX paragraphs without tables, chunks by characters, and tries unknown
formats as text. [Uploads](../backend/app/routers/documents.py) use in-process background
tasks; these jobs do not have durable retry/lease state. The path shown has no explicit
application-level upload-byte quota. Deletion commits the DB removal then attempts vector
cleanup best-effort; vector retrieval consumes index payloads without authoritative row
revalidation. Ingest/reindex/delete ordering needs reconciliation tests.

**Action:** M2 bounded parsing, optional OCR, source locations, durable jobs, and index
consistency. Test interrupted writes, parser failures, delete during ingestion, and stale
vectors. A failed vector search currently returns `[]`, which the tool describes as no
relevant passages; surface operational failure separately.

### R9 — Web search is discovery plus basic fetching, not a research product

**Medium priority; code-confirmed.** [Web tools](../backend/app/agent/tools/web.py) provide
ddgs/SearXNG results and text extraction through regular-expression HTML stripping.
`web_fetch` loads the response before truncating displayed characters. Search lacks a
first-class research plan, evidence retention, source/time scope, or completeness rubric.

The existing SSRF guard checks resolved addresses and redirects, which is valuable.
Resolution checking and the later HTTP connection are separate, however; connection-bound
DNS/egress enforcement remains a hardening item, not a demonstrated exploit in this review.

**Action:** M2 bounded fetching/extraction and evidence workflow. Keep discovery snippets
distinct from retrieved text. Start with a controlled research loop; extra sub-agents are
not a substitute for source quality.

### R10 — Projects and non-destructive revisions are missing

**Medium/high product priority; code-confirmed.** The [data model](../backend/app/models.py)
has conversations, documents, memories, assistants, and skills but no project entity.
[Message truncation](../backend/app/routers/conversations.py) deletes the selected message
and subsequent messages; edit/regenerate in the store call it before generating a replacement.
[Sidebar search](../frontend/src/components/layout/Sidebar.jsx) filters loaded titles only;
conversation listing is unpaginated. Memory has provenance to a conversation, but no
project boundary, expiry, review state, or edit service.

**Action:** M3 project context, memory controls, full-text search, and preserved branches.
Design conversation ancestry and workspace versions together so switching a branch does
not misrepresent what files exist.

### R11 — Canvas is a preview, not yet an artifact editor

**Medium product priority; code-confirmed.**
[CanvasPanel](../frontend/src/components/canvas/CanvasPanel.jsx) fetches the latest file and
renders HTML/Markdown/text. There is no artifact-version entity, direct editing, conflict
detection, or selected-section workflow. The iframe excludes same-origin access, but allows
scripts, forms, popups, and modals, and has no explicit preview network restriction here.
Its fixed minimum width and mouse resize need a mobile/keyboard audit.

**Action:** M3 versioned artifact editing and bounded preview execution. Retain parent-page
isolation; add explicit egress policy before expanding interactive previews. Do not call an
opaque iframe an offline sandbox.

### R12 — Test breadth is good; product-quality evidence is thin

**High delivery priority; code-confirmed.** [Frontend package scripts](../frontend/package.json)
only include dev/build/preview; [CI](../.github/workflows/ci.yml) builds the frontend but
does not exercise browser interactions. [Live evals](../backend/evals/run_evals.py) contain
three scenarios: HTML file creation, Python tool invocation, and hello.py creation. The
math check does not assert the answer 5050; the planning check does not assert a plan.

[ChatPage](../frontend/src/pages/ChatPage.jsx) smoothly scrolls to the bottom on every live
update, even when someone may be reading earlier content. Store, composer, chat router,
and harness each span roughly 500–600 lines and mix concerns that upcoming work will touch.
These are targeted maintainability seams, not proof the entire architecture is unmaintainable.

**Action:** F05/F10 offline browser/contract tests and outcome-based evals. Measure scroll,
long-chat rendering, accessibility, cold setup, and supported provider quality. Refactor
the boundaries as tests and new services land, not through a standalone rewrite project.

### R13 — Schema and operational guarantees need to catch up

**High delivery priority; code-confirmed.** [database.py](../backend/app/database.py) uses
`create_all` plus additive raw DDL. It cannot represent general versioned schema changes,
backfills, or controlled upgrade paths. Postgres support exists, but the current CI test
job does not configure a Postgres service. Embedded Qdrant, caches, MCP sessions, locks,
and the [process-local limiter](../backend/app/rate_limit.py) constrain scale independently.

At review start, deployment docs simultaneously described fail-closed isolation and an
obsolete fallback to local execution, mentioned `admin/admin`, and suggested backing up
Postgres instead of the data directory. Files/attachments/workspaces remain necessary with
Postgres. The architecture's extension index proposed adding containers/vector search that
already exist. Several of these contradictions are corrected alongside this plan.

**Action:** F06 migrations, coherent backup/restore, lifespan/deployment tests; M5 tested
installation and upgrade profiles. Continue to document single-process operation until
distributed state is deliberately implemented and tested.

### R14 — Privacy controls and API compatibility need explicit contracts

**Medium/high priority before broader autonomy; code-confirmed.** The
[token store](../frontend/src/api/token.js) persists a bearer token in localStorage;
session/cookie tradeoffs should be assessed with a browser threat model rather than
changed casually. Provider overlays and MCP credentials have plaintext-at-rest storage;
MCP environment values are returned by its admin API even though bearer/header values
are masked. [Guardrails](GUARDRAILS.md) intentionally preserve original local content and
do not amount to comprehensive data-governance or outbound-network control.

[API keys](../backend/app/models.py) store reserved `scopes` that are not enforced.
The [gateway](../backend/app/routers/gateway.py) supports a deliberately limited subset of
chat completions, not tools/agent operation or universal SDK semantics. A provider's
unsupported-argument errors and malformed tool JSON also need an explicit adapter/error
contract instead of accidental behavior; the OpenAI adapter turns invalid JSON into `{}`.

**Action:** M1 validated tool invocation; M4 enforced key/connection scopes and one run
service; M5 secret references, diagnostics, provider compatibility, and end-to-end egress
policy. Preserve strict private-content ownership and the documented metadata-only
chargeback exception. Keep regulated deployment separately gated.

## Roadmap traceability

| Findings | First planned work | Later product outcome |
|---|---|---|
| R1–R2 | F03/F04/F07 | Durable tasks, run receipts, controlled budgets |
| R3–R4 | F01/F02 | Predictable delegation and trusted connections |
| R5 | F04 | Bounded, inspectable project context |
| R6–R9 | F08/F09, M2 | Grounded research with verifiable evidence |
| R10–R11 | M3 | Projects, preserved alternatives, usable deliverables |
| R12 | F05/F10 | Measured user journeys and release confidence |
| R13 | F06, M5 | Tested upgrades, restores, and self-hosting |
| R14 | M1 tool contracts, M4/M5 | Explicit permissions, destinations, compatibility |

Validate the product direction with five pilot users completing the same research-to-brief
journey. The audience, effort ranges, and success thresholds in the roadmap are proposals.
They should change with observed task success, verification effort, repeat use, and operating
cost; no retention, latency, answer-quality, or competitive-superiority baseline is claimed.
