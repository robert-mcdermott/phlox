# Approval continuity and recovery

Implemented in [Wave 2](IMPLEMENTATION_WAVES.md). This guide covers interactive approval
snapshots, not the future durable worker/run service.

## User journey

When a tool needs approval, Phlox saves the pending calls, conversation context, effective
model/settings/tools, consumed rounds, and accumulated top-level token usage. Open the
conversation again after a reload or app restart to recover the approval card. Review the
arguments, then **Approve & run**, **Deny**, or **Dismiss**. Approve and Deny continue the
same turn; Dismiss closes the pending turn without running its waiting tools.

Approvals expire **24 hours after each pause**. An expired approval or a snapshot from
before Wave 2 can be inspected and dismissed, but cannot be resumed. Older snapshots lack
reliable execution counters; Phlox does not guess their remaining budget.

If provider setup or a budget/policy check rejects a resume, the approval stays pending.
The error remains visible and the card is recovered. **Refresh status** reloads both
messages and approval state, including results completed in another tab. Pending approvals
block new sends, edits, and regeneration in that conversation until resolved or dismissed.
If older data has multiple unresolved approvals, the UI presents the oldest first.

## Claim and outcome contract

`PendingApproval.status` has an additive startup schema upgrade; existing rows default to
`pending`. Claiming uses a database compare-and-set from `pending` to `claimed`, including
an expiry condition. Only the winning request can dispatch tools. Every decision must be
`allow` or `deny`, and the IDs must match the complete pending batch exactly.

| Status | Meaning and recovery |
|---|---|
| `pending` | Waiting for a decision; owner may resume or dismiss while valid |
| `claimed` | Execution has been claimed. It may still be running, or its outcome may be unknown. Never automatically replay or dismiss it |
| `paused` | This claim produced another approval; the new snapshot carries cumulative state |
| `completed` | The resumed turn finished |
| `limit_reached` / `blocked` / `cancelled` | Turn ended at the round limit, an output guardrail, or an observed cancellation |
| `failed` / `interrupted` | Execution failed or its generator closed before confirming completion. Some actions may have run; inspect results, then dismiss the notice |
| `dismissed` | Closed by the owner; cannot be resumed |

`expired` and `legacy` are display states for a pending row; they do not change the claim
history. `done` SSE frames include `outcome` for finalized turns. Approval outcomes persist
on their snapshot rows; fresh turns without approvals do not yet have durable run records.

A **process crash after claiming** leaves `claimed` intact. Phlox cannot distinguish a
completed remote action from an unexecuted one and does not reset the claim on startup.
Inspect workspace/remote results before deciding what to do in a **new conversation**.
The original conversation stays blocked while its claim is unconfirmed. A worker lease,
run-event history, and reconciliation workflow are F07 work. Cancellation is best effort;
it never proves that a remote action was undone.

## Policy and budget checks

Before claiming, the API checks current authentication/ownership (including account status
through the auth dependency), expiry, assistant visibility, assistant capability limits,
registered/enabled tool policy, current input guardrails, provider construction, and the
current monthly budget. An explicit Allow cannot override a disabled or denied tool.
The saved tool set is intersected with current permissions; resuming cannot widen it.
Revoking access to an assistant rejects the whole resume because its saved prompt may
already contain knowledge-base excerpts.

The turn's original `max_tool_rounds` is cumulative across approvals. A lower current user
limit can restrict it; raising that setting cannot extend the saved limit. Each provider
round consumes one round before streaming. A pending action from the last allowed round
can finish, but no further provider round starts. If a newly lowered limit is below the
already-consumed count, even those pending actions are skipped.

Top-level usage survives every pause and is recorded once when the turn finalizes. Resume
budget checks include the paused turn's known cost in addition to finalized ledger spend.
Dismissing a still-pending turn records its known cumulative usage with an idempotent
`approval-dismiss:<id>` ledger key, in the same transaction as dismissal. Dismissing an
already-finalized failure does not bill its snapshot again. The ledger remains metadata
only; dismissal does not create a completed assistant answer in the transcript.

This is not a cost reservation system. Other pending/in-flight turns, children, compaction,
unknown provider usage, and mixed-model/rate-snapshot attribution still require F04.
Failed or interrupted execution before finalization may contain unrecorded usage after
its last snapshot. A turn can still exceed a monthly cap while running.

## API and data ownership

- `GET /api/chat/approvals/{conversation_id}` lists unresolved approval projections for
  the owner: pending calls, assistant preamble, completed tool steps, artifacts, usage,
  expiry, and status. It never returns canonical history, system prompts, or settings.
- `POST /api/chat/approve` takes `{pending_id, decisions}`. Duplicate/closed/expired claims
  return 409; malformed decisions return 422; budget rejection returns 402; unavailable
  provider setup returns 503. Those pre-claim errors dispatch no tools.
- `DELETE /api/chat/approvals/{pending_id}` dismisses a pending, failed, or interrupted
  snapshot with its own atomic status check. A claim in progress cannot be dismissed.

Other users, including admins, receive **404**. Snapshots remain associated with the
conversation for status inspection and are deleted by the ORM when the conversation or
its owner's data is deleted, including on SQLite without foreign-key enforcement.

The UI assigns a generation number to each stream. Stop, chat selection, a new stream,
and logout invalidate earlier callbacks. Reconciliation also checks the generation after
awaiting network responses, so an old stream cannot overwrite a newer conversation.

## Verification

[Backend regression tests](../backend/tests/test_approval_continuity.py) use scripted
providers and isolated databases. Coverage includes multiple pauses, exact decisions,
simultaneous claims, policy revocation, budgets, limits, expiry, ownership, failed setup,
terminal outcomes, dismissal accounting, deletion, and an idempotent old-schema upgrade.
SQLite is exercised here; the status SQL is portable, but a Postgres integration run is
still part of the later migration/restore wave.

[Browser tests](../frontend/tests/browser/approval.test.js) use Node's test runner,
Playwright Chromium, and the real SPA served on an ephemeral localhost port. Each test has
its own browser context and synthetic API fixtures. External HTTP requests are blocked;
there are no cloud model calls or connections to a running Phlox backend. These complement
the real backend API tests; they are not full-stack live-provider certification.

```bash
cd backend
uv run --frozen --extra dev ruff check app tests
uv run --frozen --extra dev pytest
cd ../frontend
npm ci
npx playwright install chromium
npm run test:browser
npm run build
```

CI installs Chromium with `--with-deps` and runs the browser suite alongside the build.
