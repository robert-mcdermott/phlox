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

## Wave 3 — Model-call accounting and context fit (proposed next)

F04: capture usage at each model-call seam, attribute it to the actual model and parent
turn, preserve known partial usage, snapshot prices, distinguish unknown from free, and
reconcile without double-counting. Include compaction and children. Add a final context-fit
check that accounts for tool schemas, tool output, images, and reserved output tokens.
Expand the browser harness only for affected user-visible flows.

## Later waves (re-estimate after Wave 3)

F06 migration/restore baseline; then F07 durable runs and F08 source citations. Ship the
first research improvement before expanding into the project's longer-term autonomy features.
