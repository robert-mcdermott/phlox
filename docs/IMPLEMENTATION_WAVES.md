# Implementation waves

Track bounded implementation increments against [ROADMAP.md](ROADMAP.md). Milestones
span multiple waves; completing a wave does not imply its milestone is complete.

## Wave 1 — Predictable tool connections and delegation

**Status:** implemented and verified, 2026-09-07. **Scope:** F01, F02, and the
permission-default dependency from M1.1. No persistent schema changes.

- [x] MCP reconnect without deadlock; failed/expired initialization and disconnect clean
  up their session tasks and registered tools. Shut down the MCP loop with the app.
- [x] Forward Stop to MCP calls; cancel local work on timeout and report uncertain remote
  outcomes without automatically retrying actions.
- [x] Unseeded tools respect their declared permission; unknown tools are denied.
- [x] Children inherit the resolved parent provider/model, generation parameters, allowed
  tools, owner, assistant scope, and cancellation. No fallback to global user settings.
- [x] Explicit read-only children can run in a bounded pool; children with mutation
  capability execute sequentially within the parent turn. Limit child calls per round
  and skip queued work after Stop.
- [x] Add offline regression coverage, run the full checks, and update extension docs.

Acceptance: repeated MCP connect/disconnect/failure leaves no stale tools or transport
tasks; cancelled MCP calls stop local waiting and request cancellation; two users' children
keep their own parent context; disabled tools cannot reappear in children; concurrency
limits and sequential mutation behavior hold under scripted providers.

### Delivered behavior and evidence

- MCP connection/session/call state is owned by the background loop. Each transport is
  entered/exited by the same task; reconnect cannot replace a session still cleaning up.
  Exact tool ownership and registry snapshots avoid interference during lifecycle changes.
- Child context follows the actual parent model/profile (including a known fallback),
  with an independent DB session and current ownership/assistant checks. Missing parent
  context is rejected. `read_only: true` excludes every built-in mutation/exec tool.
- Limits: **3 read-only workers per batch, 8 child requests per round**. Mutating children
  execute sequentially within the parent turn. Queued children are skipped on cancellation.
- Tests: [MCP lifecycle](../backend/tests/test_mcp_lifecycle.py),
  [delegation context](../backend/tests/test_delegation_context.py), and updated
  [permissions](../backend/tests/test_permissions.py),
  [harness](../backend/tests/test_harness.py),
  [sub-agent](../backend/tests/test_subagent.py), and
  [assistant](../backend/tests/test_assistants.py) contracts. Includes a real local stdio
  MCP server; no cloud model calls or user-configured integrations were exercised.
- Verification: `uv run --frozen --extra dev ruff check app tests` passed;
  `uv run --frozen --extra dev pytest` → **215 passed**, 9 existing deprecation warnings,
  13.77 s; `npm run build` passed (existing large diagram-chunk warning); `git diff --check`
  passed. The baseline had 179 tests; this wave adds 36 cases and strengthens existing ones.

Remaining boundaries: cancellation cannot prove a remote side effect was rolled back;
SSE/HTTP MCP deployments still need real-server interoperability checks. Concurrency limits
are per parent round, not deployment-wide. Separate top-level runs can still share a
conversation workspace. Child usage attribution, cumulative resume budgets, parent run IDs,
project context, general tool-argument schema validation, and durable recovery remain in
later waves. F02 is complete for the current conversation-based execution model.

## Wave 2 — Approval continuity

**Status:** implemented and verified, 2026-09-07. **Scope:** F03 and the first F05 browser
harness. Adds `pending_approvals.status` through the existing additive startup upgrade.
See [APPROVALS.md](APPROVALS.md) for the user journey, API, status semantics, and limits.

- [x] Preserve consumed rounds, effective context, and cumulative top-level token usage
  across multiple pauses; honor a newly lowered current round limit.
- [x] Atomically claim approvals once, validate exact decisions, enforce a 24-hour expiry,
  and recheck current owner/account, assistant, tool, guardrail, and budget policy.
- [x] Restore pending approvals after reload/reopening; keep rejected approvals visible;
  refresh results finished in another tab; expose uncertain execution without replay.
- [x] Record known usage on dismissal once; prevent stale dismissal from charging a turn
  already claimed/finalized by another request. Delete private snapshots with their owner
  or conversation, including on SQLite without FK enforcement.
- [x] Block history changes while an approval is unresolved. Ignore stale SSE callbacks
  and stale reconciliation responses after Stop, navigation, logout, or a new stream.
- [x] Add isolated Chromium coverage to CI for login/setup → stream → reload → approval,
  budget rejection/retry, expired/claimed states, and Stop/chat-switch isolation.

Verification: **246 backend tests** (31 new approval cases), lint, **4 Chromium browser
scenarios**, and the production frontend build pass. Backend tests use scripted providers;
browser tests use synthetic API fixtures and independent contexts. Existing backend
deprecation warnings and the large diagram-chunk build warning remain.

Remaining boundaries: a process crash after claim leaves an explicitly unconfirmed claim;
it is never automatically replayed or released. Inspect results and continue in a new
conversation. Fresh runs are still request-bound; this wave does not serialize concurrent
initial sends or add worker leases/replay. Cost reservations, full model-call attribution,
child/compaction usage, interrupted usage after the last snapshot, and Postgres integration
verification remain later work. Pre-Wave-2 approvals must be dismissed and requested again.

## Wave 3 — Model-call accounting and context fit

**Status:** implemented and verified, 2026-09-07. **Scope:** F04 plus affected F05 browser
coverage. Adds nullable usage-ledger columns through the existing startup upgrade.
See [MODEL_CALLS.md](MODEL_CALLS.md) for the data contract and operating limits.

- [x] Record top-level, compaction, child, fallback, gateway, and probe generation calls
  before dispatch; retain reported usage on error, Stop, and closed streams.
- [x] Attribute actual models and parent turns with independent child sessions; record
  explicit compatibility retries separately and avoid adding cumulative snapshots twice.
- [x] Snapshot standard/cache rates; show unknown usage/pricing distinctly from zero
  in message receipts, chargeback, and CSV. Reconcile both usage APIs with the ledger.
- [x] Carry ledger turn IDs through version-3 approvals; import version-2 counters once;
  avoid duplicate charges on resume, dismissal, finalization, and startup backfill.
- [x] Check current budgets at the call seam; check context including schemas, images,
  tool results, and reserved output. Bound compaction; visibly shorten provider-bound
  tool results or reject requests that cannot fit before dispatch.

Verification: **278 backend tests** (32 new cases), Ruff lint, **6 Chromium scenarios**
(two new accounting/pricing journeys), production frontend build, documentation link checks,
and `git diff --check` pass. Backend providers and browser API fixtures are synthetic; no
live cloud calls or user-configured integrations were exercised. Existing nine backend
deprecation warnings and the large diagram-chunk build warning remain.

Remaining boundaries: tokenizer estimates need headroom; SDK-internal retries and embedding
usage remain outside this seam. Unknown usage cannot be reconstructed after process death.
Budget reservations, worker recovery, provider invoice reconciliation, and migration tooling
remain later work. New metadata does not add administrator access to private content.

## Wave 4 — Migration and restore baseline

**Status:** implemented and verified, 2026-09-07. **Scope:** F06 and the corresponding
M1.3 deployment checks. No new product entities. See [BACKUP_RESTORE.md](BACKUP_RESTORE.md).

- [x] Replace ad hoc startup ALTERs with a frozen Alembic baseline and checked adoption of
  known prior SQLite/Postgres schemas. Repair missing known indexes; reject schema drift
  and unknown revisions. Roll back failed DDL and revision updates together.
- [x] Serialize migration operations and hold a single-process maintenance lock through
  server startup/lifespan. Readiness includes the schema revision; failures stop bootstrap.
- [x] Add operator CLI commands for schema status/check/upgrade, offline backup, verification,
  restore into new destinations, and offline rebuild from stored embeddings.
- [x] Bundle database, uploads, images, workspaces/real Git checkpoints, empty directories,
  config/DB overlays, version metadata and secret environment names. Verify checksums and
  refuse unsafe paths, existing targets, and active cooperative writers.
- [x] Restore SQLite and Postgres fixtures, retain ownership/permissions/usage/claimed
  approvals, and rebuild a local vector index with ownership filters intact. Isolate restored
  database/vector paths from source infrastructure. Add a Postgres 16 CI service/drill.

Verification: **312 backend tests passed, 1 non-applicable SQLite case skipped**, with a
live disposable Postgres 16 service enabled (35 new operations cases). **6 Chromium
scenarios**, Ruff, production frontend build, documentation link checks, CI YAML parsing,
and `git diff --check` pass. Postgres fixtures use native version-16 dump/restore clients;
restored local Qdrant retrieval and real Git checkpoints are checked. Existing nine backend
deprecation warnings and the diagram chunk-size warning remain. No user deployment or
live model was used; the disposable Postgres container was removed afterward.

Compatibility follow-up: an actual pre-Alembic SQLite installation retained the original
`usage_ledger.message_id VARCHAR(32)` declaration. The original Wave-4 check rejected it.
The baseline now recognizes only that known old width, and revision `0002_ledger_width`
widens it transactionally while preserving uniqueness and all data. Check/backup also
accept known older revisions so operators can back up before upgrading. An isolated copy
of the affected database upgraded with all existing table rows unchanged; the original was
read-only. Regression coverage includes both engines, already-stamped baselines, long SQLite
IDs, null IDs, duplicate rejection, unrelated width rejection, and conversion rollback.
Follow-up verification: **319 backend tests passed, 1 non-applicable case skipped**,
including Postgres 16; Ruff and `git diff --check` pass. The original database now passes
read-only `db check` and remains unmodified pending the operator’s backup/start sequence.

Remaining boundaries: this is offline recovery, not hot backup/PITR. External writers and
older releases must be stopped explicitly; external secrets and remote side effects need
separate recovery. Postgres and filesystem publication are not one atomic transaction;
a failed restore may leave its newly provisioned target DB populated. Only trusted bundles
are supported. Cross-engine conversion, automatic destructive downgrades, Windows ACL/lock
integration testing, provider invoice reconciliation, and durable worker recovery remain
later work. The verified database service is Postgres 16, not every managed deployment.

## Wave 5 — Reconnectable runs

**Status:** implemented and verified, 2026-09-07. **Scope:** the first bounded F07 delivery
and its F05 browser coverage. Builds on Wave 3 accounting and Wave 4 migrations/recovery.

**User outcome:** start a task, refresh or switch conversations, then return to its saved
progress or completed answer. Explicit Stop requests cancellation. Server restart exposes
interrupted work and unresolved actions without silently executing them again.

With `runs.enabled: true`, a server-owned worker persists and completes chat independently
of its subscribers. The default remains the legacy request-bound mode. See [RUNS.md](RUNS.md)
for the rollout flag, API, limits, retention, cancellation, and recovery contract.

### Delivered scope

| Task | Deliverable | Acceptance |
|---|---|---|
| W5.1 — Persist run state | Add `Run`, `RunEvent`, and `ToolExecution` through a new Alembic revision. Define owner, conversation, execution context version, states/reasons, and ordered event IDs; associate existing call accounting with the run without rebilling. | Populated SQLite/Postgres upgrade and restore preserve old messages, approvals and ledger entries. Other users, including admins, receive 404 for private run data. |
| W5.2 — Own execution on the server | Add a bounded database-backed queue and one in-process worker with its own DB sessions. Atomically enforce one unresolved run per conversation, bound pending work per user/deployment, and deduplicate retried create requests. | Two tabs or retried requests cannot create duplicate execution. Queue exhaustion is visible and bounded. Current account/tool/budget policy is checked before execution. |
| W5.3 — Separate actions from subscription | Add run creation/status, event subscription with a cursor, and explicit cancellation. Keep `/api/chat` and approval clients working through an adapter; persist events before delivery and batch token/progress writes with byte limits. | Disconnecting a subscriber leaves the worker running. Reconnection replays ordered progress without duplicate messages/tool results. Stop acknowledgement is distinct from confirmed cancellation. |
| W5.4 — Integrate approvals and interruption | Link approval pauses/resumes to the same run; retain atomic claim and cumulative limits. Persist tool intent before dispatch and result afterward. On startup, mark abandoned running/claimed work interrupted or outcome-unknown. | Restart at an approval preserves the pending review; restart around a mutating call never replays it automatically. Old unlinked approvals remain compatible. Failed/unknown outcomes remain visible. |
| W5.5 — Recover the browser journey | Separate the selected conversation's subscription from server execution. Add running/waiting/interrupted indicators, restore progress on reopening, deduplicate by run/event ID, and send Stop to the cancellation endpoint. | Refresh, chat switch, two tabs, temporary network loss and re-login recover the correct owner's state. Logout detaches and clears private client state. |
| W5.6 — Verify and document rollout | Add scripted backend and browser journeys, event-storage limits and cleanup rules, run-data deletion coverage, backup/restore drills, and an opt-in rollout flag. | The complete create → disconnect → reconnect → approve/Stop → finish journey passes; run content is deleted with its owner/conversation while usage metadata retains its existing policy. |

Implemented limits: one worker; 32 unresolved runs per deployment / 4 per user / 1 per
conversation; 8 MiB request snapshots; 2 MiB event logs / 128 KiB per event; bounded live
preview buffers; terminal replay expiry after seven days. Interrupted actions require
explicit acknowledgement. Linked approvals retain the existing claim and accounting seam.
Known older schema revisions remain checkable/back-upable before upgrading to `0003_runs`.

Verification: **348 backend tests passed, 1 non-applicable SQLite case skipped**, with
Postgres 16 enabled (**29 new cases**). **9 Chromium browser scenarios**, Ruff, production
frontend build, documentation link checks, and `git diff --check` pass. Coverage includes
idempotent admission, detach/replay, explicit Stop, successive approval claims, current
policy rejection, child action evidence, bounded progress/log overflow, startup recovery,
account/conversation deletion, and populated upgrade/restore on both engines. Browser
fixtures cover lost acceptance, network reconnect, reload, two tabs, approvals, Stop,
interruption acknowledgement, logout, and login recovery. Existing nine backend
deprecation warnings and the diagram chunk-size build warning remain. Tests use isolated
data and scripted providers; no live model/provider certification is claimed. The disposable
Postgres test container was removed after verification.

Boundaries: one application process/worker; no broker, distributed leases, scheduled tasks,
automatic continuation after worker death, or automatic retry of uncertain tool actions.
Keep the existing provider/tool interfaces and permission gate. The OpenAI gateway remains
its current completion API. This wave delivers the reconnect journey, not all of M1.2.

## Wave 6 — Document sources and clickable citations

**Status:** implemented and verified, 2026-09-07. **Scope:** the document portion of F08,
with upload/assistant-KB evidence and recovery coverage from F05. Web evidence remains later.

**User outcome:** inspect the exact retained passage behind an answer, across repeated
searches, direct references, approval pauses, reloads, and Markdown exports. This feature
works in both chat modes without an additional flag or service. See [SOURCES.md](SOURCES.md)
for usage, APIs, retention, and deletion boundaries.

| Task | Deliverable | Acceptance |
|---|---|---|
| W6.1 — Register evidence | Add private `Source`/`SourceUse` records and message citation metadata in `0004_sources`. Use stable conversation labels and canonical SQL chunks; associate captures with the shared accounting turn. | Repeated searches and concurrent children reuse the same identity; changed evidence receives a new label. Foreign or stale vector payloads cannot become evidence. |
| W6.2 — Connect chat paths | Register direct references and document-search results before supplying their labels. Emit source catalogs and preserve them through approvals and durable replay. | A scripted turn directly references one document, searches twice, pauses for approval, and finishes with correct S1/S2 bindings in both chat modes. Unknown labels stay unverified. |
| W6.3 — Inspect and export | Render citation buttons and an accessible source panel with the captured excerpt, location, date, truncation and changed-document notices. Add a reauthorized Markdown source appendix. | Browser reload/new-tab/replay preserve identity; code remains unchanged; Escape restores focus. Export and panel reads hide unavailable source content. |
| W6.4 — Bound and protect snapshots | Enforce ownership/assistant visibility at capture and read, bounded excerpts/source counts, 30-day expiry, document-deletion purges, and conversation/owner cascades. | Copied IDs return 404 across users, including admins. Revocation hides snapshots; document and assistant deletion clear excerpt/query content; account deletion removes source rows. |
| W6.5 — Verify upgrades and recovery | Freeze the Wave-5 schema additions for pre-upgrade checks/backups; upgrade populated SQLite/Postgres instances and restore citation-bearing backups. Update architecture, operator, and extension guides. | Prior messages remain intact and nullable citations stay compatible; restored references resolve the same passage and still honor deletion. |

Verification: **363 backend tests passed, 1 non-applicable SQLite case skipped**, with
Postgres 16 enabled (**15 new cases**). **11 Chromium scenarios**, Ruff, frontend production
build, documentation link checks, and `git diff --check` pass. Scripted providers and isolated
fixtures require no model credentials. Existing nine backend deprecation warnings and the
diagram chunk-size build warning remain. No live-model citation-quality certification is claimed.

Boundaries: uploaded/assistant documents only; chunk-relative locations, not PDF page
coordinates. Valid references do not prove claim support. Snapshot removal does not rewrite
historical answer/tool text, pending context, exported files, or backups. Retention is bounded
in code; worker-off deployments physically purge expired snapshots at startup and deny them
on every read. Web capture, artifact-version references, richer ingestion provenance, and
semantic citation evaluation remain future work.

## Wave 7 — Reliable document ingestion and richer source locations

**Status:** implemented and verified, 2026-09-08. **Scope:** a bounded portion of M2.2,
building on Wave 6's document evidence. See [INGESTION.md](INGESTION.md) for the user and
operator guide, including the upgrade procedure for existing libraries.

**User outcome:** track processing, retry interrupted uploads, inspect real page/section
locations, retrieve DOCX table contents, and migrate embedding models explicitly while
preserving the previous index on failure. No new service or runs flag is required.

| Task | Deliverable | Acceptance |
|---|---|---|
| W7.1 — Preserve extraction provenance | Bounded PDF pages, Markdown/DOCX headings and DOCX tables; hashes, offsets and parser/chunker versions; richer citation panel/export locations. | Real PDF/DOCX fixtures retain pages and table facts; legacy evidence identities survive migration. |
| W7.2 — Make processing durable | SQL queue/attempt/progress, one document worker, upload/retry/reprocess UI for personal and assistant documents, interrupted recovery. | Restart requires explicit retry; duplicate active requests are rejected; successful retry replaces chunks; deletion prevents publication. |
| W7.3 — Track embedding identity | Provider/profile/model/version/endpoint fingerprint and dimensions; strict provider batch validation. | Same-dimension changes are detected; outages never publish hash substitutions; invalid/incomplete vectors fail safely. |
| W7.4 — Publish indexes safely | Explicit admin rebuild with progress; separate staged collection, SQL vector/pointer publication, authorized keyword degradation. | Provider/staging failures preserve the previous index; copied/private/stale hits cannot supply evidence, including assistant revocation during embedding. |
| W7.5 — Verify upgrade and retrieval | Frozen Wave-6 schema, additive `0005_ingestion`, SQLite/Postgres migration/restore drills; versioned retrieval fixtures and browser recovery checks. | Old chats/citations survive, backups restore new metadata, DOCX table recall improves over paragraph-only extraction, UI exposes retry and rebuild failure. |

Verification: **382 backend tests passed, 1 non-applicable SQLite case skipped**, with
Postgres 16 enabled (**19 new cases**). **12 Chromium scenarios**, Ruff, frontend production
build, documentation checks, and `git diff --check` pass. Tests use isolated databases,
real local Qdrant, actual parser fixtures, and scripted providers. Existing dependency
deprecation warnings and the diagram chunk-size build warning remain.

Boundaries: no OCR, PDF layout inference, cross-upload deduplication, separate ingestion
cancel button, hard parser isolation, staging collection garbage collector, or distributed
worker. Processing timeouts are cooperative. Full re-embedding is bounded to 10,000 ready
chunks and 8 million vector components; limits and recovery are documented. Old source
snapshots retain their original evidence. Keyword/paraphrase fixtures establish behavior,
not a representative live-model retrieval score. Memory keeps its separate embedding path.

## Wave 8 — Captured web evidence and reliable fetching

**Status:** implemented and verified, 2026-09-08. **Scope:** fetched HTML/text evidence
from M2.1/F08 and the bounded fetch prerequisites for M2.3. Existing source tables support
this wave; there is no schema revision or new flag. See [WEB_SOURCES.md](WEB_SOURCES.md).

**User outcome:** inspect the actual web passages behind an answer, distinguish discovery
snippets and failed fetches from evidence, preserve references through pauses/reloads, and
remove retained web snapshots without reassigning their citation labels.

| Task | Deliverable | Acceptance |
|---|---|---|
| W8.1 — Capture web evidence | Normalized URL, title, fetch time, extracted content hash, bounded passages and stable S-labels in the existing private registry. | Identical fetches reuse labels; changed text gets new identities; snippets never masquerade as fetched evidence. |
| W8.2 — Bound and harden fetching | Validated numeric-address connections, original Host/TLS hostname, redirect checks, body/time limits, bounded DNS tasks and Stop. | No second hostname lookup at connection; private/transition targets fail closed; oversize/incomplete downloads and cancellation publish no passage. |
| W8.3 — Explain unavailable pages | Explicit HTTP/connection/format/limit failures and possible access barriers, with no captured response body as evidence. | Denied, paywalled, empty, unsupported and interrupted fixtures produce truthful errors; unavailable citation panels show no excerpt. |
| W8.4 — Inspect, retain, export | Web source panel with original URL, fetch time, retained passage, differing-version notice and snapshot removal. Shared retention and Markdown export. | Foreign users/admins receive 404; deletion/expiry removes retained content; old transcripts remain explicitly separate. |
| W8.5 — Verify persistence | Scripted approval/durable replay, SQLite/Postgres backup restoration, real local HTTP fixtures and Chromium scenarios. | References resolve to the same retained web passage across pauses, new tabs and restored backups. |

Verification: **416 backend tests passed, 1 non-applicable SQLite case skipped**, with
Postgres 16 enabled (**34 new cases**). **15 Chromium scenarios**, Ruff, frontend production
build, documentation checks and `git diff --check` pass. One isolated, credential-free live
HTTPS fetch of example.com verified the real TLS/extraction path. No user model calls or
live data were used. Existing dependency deprecation and diagram chunk-size warnings remain.

Boundaries: 2 MiB bodies, 30-second fetch deadline, 3-second socket operation timeout,
five redirects, four unfinished OS DNS tasks, and 20,000 extracted characters split into
6,000-character passages. No environment proxies, cookies, authenticated browsing, compressed
responses, JavaScript rendering, web PDF parsing, universal paywall detection, semantic
claim verification, or autonomous Research mode. Snapshots remain private for 30 days;
source removal does not rewrite messages, run events, exports or backups. A stopped OS
resolver may finish in a bounded daemon task, but cannot initiate a connection.

## Wave 9 — Opt-in Research and admin-managed search

**Status:** implemented and verified, 2026-09-08. **Scope:** bounded M2.3 Research mode,
M1 tool argument validation, and targeted draft/scroll UX. No schema migration or new
configuration-file flag. See [Research mode](RESEARCH.md) for setup and manual verification.

| Task | Delivered behavior | Acceptance |
|---|---|---|
| W9.1 — Explicit Research choice | Chat is the default; select Research, source scope, depth, and optional domains. Existing deep-research skills remain guidance, not a mode switch. | New chats/turns stay Chat; regeneration retains the original Research choice. |
| W9.2 — Staged research | Provider-neutral planning, bounded sequential gathering/cross-checking, then a reserved synthesis pass. | Planning/report stages cannot dispatch tools; read counts, duplicates, passes, time and reported-token thresholds constrain gathering. |
| W9.3 — Source scope | Selected document intersection and web domain/redirect checks, fresh question context, no memory injection or child/MCP/exec tools. | A model cannot widen document selection or fetch beyond domain scope; current policy still controls approvals. |
| W9.4 — Search administration | Live DDG/Serper/public HTTPS SearXNG selection, write-only key entry/removal, unsaved test, paced requests, provider cooldown, one DDG fallback. | Keys never return through admin reads; fallback is explicit; cancellation prevents fallback; malformed/private/oversized service responses fail closed. |
| W9.5 — Visible continuity | Plan/stage/source counters/available usage, saved report metadata and citations, approval and durable event replay, partial completion notices. | Stop causes no synthesis; reload replays the same progress; available evidence survives export. |
| W9.6 — Chat UX | Per-conversation session drafts, clear on logout, scroll following only near the bottom, keyboard-accessible Jump to latest. | Switching/reloading restores text without carrying Research mode or attachments; readers can scroll during generation. |

**Verification:** 444 backend tests passed, 21 optional cases skipped; all 20 Chromium
scenarios, Ruff, frontend production build, local documentation links and `git diff --check`
pass. Desktop and phone Research controls and the admin search form were visually reviewed
using isolated API fixtures. Existing deprecation and large-bundle warnings remain. No paid
provider calls or user credentials were used. Scripted fixtures do not establish live-model
research quality or public search uptime; the guide includes a configured-provider test.

Boundaries: time/reported-token thresholds apply between operations; final synthesis and
in-flight requests may exceed them. Search credits are separate from model costs. Public
SearXNG JSON availability varies; no instance is automatically chosen or scraped from the
directory. Legacy config remains a seed until an admin saves search settings. The initial
workflow is sequential, uses selected personal documents and fetched HTML/text, and does
not add OCR, date-range filtering, parallel researchers, project context, artifact editing,
or scheduling. Ordinary chat and existing editable skill records remain available.

## Wave 10 — Automatic model discovery

**Status:** implemented and verified, 2026-09-08.

**Scope:** model discovery and provider setup UX. Projects are deferred by user choice.
No schema migration, new dependencies, model downloads or inference calls are needed for
discovery. See [setup and manual verification](MODEL_DISCOVERY.md).

| Task | Delivered behavior | Acceptance |
|---|---|---|
| W10.1 — Live catalogs | Native Ollama/LM Studio, generic OpenAI-compatible and Bedrock listing adapters | Models added outside Phlox appear on picker open or Refresh; listing never invokes generation |
| W10.2 — Shared picker | Search, keyboard selection, custom IDs, and provider-reported details in Model settings, assistants and admin defaults | Current selections survive missing models and failed refreshes; known embeddings are excluded |
| W10.3 — Admin setup | Unsaved discovery with secret preservation; Automatic/Curated and API selection | Preview does not save; existing explicit model lists remain curated; saved secrets never return |
| W10.4 — Reliability | Configuration/credential-isolated bounded cache, refresh coalescing, safe errors and last-success retention | Failed or malformed catalogs cannot replace the last successful list or disclose provider errors/secrets |
| W10.5 — Phone settings | Compact section selector replaces the sidebar on small screens | Model choices and provider setup fit the phone content width |

**Verification:** 478 backend tests passed, 21 optional cases skipped; all 23 Chromium
scenarios, Ruff, frontend production build, documentation links and `git diff --check`
passed. Desktop model selection and phone selection/provider setup were visually reviewed
using synthetic catalogs. Existing deprecation and bundle-size warnings remain. No live
provider credentials, model generation, downloads or user data were used in these checks;
the discovery guide includes live-provider verification steps.

Boundaries: metadata does not prove tool/vision support, generation access, or effective
context length. Generic/Ollama catalogs leave unsupported metadata unknown. LM Studio may
require model loading/JIT; Bedrock listing requires AWS metadata permissions and compatible
invocation access. Bedrock bearer-key profiles use curated IDs. The gateway's `/v1/models`
continues to expose its configured catalog. Curated choices are not an authorization boundary.

## Later wave — Projects and inspectable context

Introduce private projects with linked chats, selected knowledge and instructions, then
show users which context is used. Build versioned artifacts and preserved conversation
alternatives as the next deliverable wave. Continue measuring Research quality with real
configured models before increasing autonomy or making competitive quality claims.
