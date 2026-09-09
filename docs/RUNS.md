# Reconnectable runs

[User Guide](USER_GUIDE.md) · [Project overview](../README.md)

[Wave 5](IMPLEMENTATION_WAVES.md) adds an opt-in server worker for interactive chat.
A submitted task keeps running when you refresh, switch chats, close a tab, or lose the
network. Reopen the conversation to see saved progress or the completed transcript.
**Stop** requests cancellation; the UI keeps showing **Stopping** until execution returns.
Cancellation cannot undo an action that already happened on a remote service.

## Enable it

Stop Phlox and make an [offline backup](BACKUP_RESTORE.md) using the same database/data
configuration you normally use. From `backend/`, for example:

```bash
uv run -m app.ops db check
uv run -m app.ops backup --output /backups/phlox-before-wave5 --stopped
```

Add this to `backend/config.yml` (or your `PHLOX_CONFIG` file), then start Phlox normally:

```yaml
runs:
  enabled: true
```

This is a **file-only, restart-required** setting. It defaults to false. Startup upgrades
the configured database to the current head, `0008_artifacts`, including when the flag is off.
Revision `0003_runs` introduced run tables; `0004_sources` adds citations and `0005_ingestion`
adds document processing/provenance metadata. `0006_projects` adds [projects and context records](PROJECTS.md). `0007_branches` preserves
[conversation alternatives](CONVERSATION_ALTERNATIVES.md), including the parent of resumed answers.
`0008_artifacts` adds [editable artifact versions](ARTIFACTS.md); selected-text revisions
are request-bound and stop on editor disconnection independently of this flag.
The [document worker](INGESTION.md) is always
available independently of `runs.enabled`; chat Stop does not cancel document processing.
No manual stamping or new database is required. Known Wave-4 revisions remain checkable
and back-upable before upgrade. Use the normal `./scripts/start.sh dev` or `prod` command
from the repository root after editing the file. The `prod` launcher also requires the
[production secret and sandbox setup](USER_GUIDE.md#start-stop-and-production-preparation).

Use **one application process** per database/data directory. The existing maintenance
lock is held until the worker and tool threads finish. Shutdown asks active work to stop
and waits for it; an unresponsive custom tool can delay shutdown. Do not add Uvicorn workers.
There is no broker, distributed lease, scheduled execution, or automatic action retry.
The OpenAI-compatible gateway keeps its existing request/response contract.

To turn the feature off, stop Phlox, set `runs.enabled: false`, and restart. Saved runs
remain inspectable, cancellable, and acknowledgeable in the UI. Linked approvals require
the feature to be enabled again before resuming. Turning the flag off does not drop data.

## Run and restart behavior

One worker selects queued requests in creation order. It owns its SQLAlchemy sessions;
HTTP subscribers use separate short-lived sessions. A run stores a version-1 request
snapshot with its selected conversation/profile/model and user options. At execution,
Phlox rebuilds history and resolves current settings, account, assistant, tool, guardrail,
and budget policy through the same functions used by ordinary chat. The user message and
attachments are saved when the worker prepares the turn. A queued request can be inspected
under **Submitted request**; an unstarted request is retained if restart interrupts it.

A conversation admits only one unresolved run. Creation, admission limits, and state
changes serialize in the single process; a unique database constraint additionally
protects conversation ownership and `(user_id, request_key)` deduplication. History edits,
regeneration, and conversation setting changes are blocked while work is unresolved.
Deletion of a conversation/account with queued or executing work returns 409: explicitly
Stop and wait for confirmation first. Terminal and paused private records cascade with
their conversation or owner, including on SQLite without foreign-key enforcement.

| State | Meaning |
|---|---|
| `queued` | Accepted and waiting for the worker |
| `running` | Worker owns this attempt |
| `awaiting_approval` | Saved decision required; no worker slot is occupied |
| `cancel_requested` | Stop accepted; execution has not yet confirmed termination |
| `completed` | Harness saved its final answer |
| `cancelled`, `failed`, `blocked`, `limit_reached` | Work ended with the stated outcome/reason |
| `interrupted` | Completion or an action outcome is unconfirmed; review evidence before acknowledging |

On startup, queued/running/stopping work is **never automatically dispatched again**.
A saved, unclaimed approval is restored to `awaiting_approval`, including a pause committed
just before its event was saved. Previously submitted but unclaimed decisions are discarded;
review and submit them again. Other abandoned work becomes `interrupted`. Started tool
records without a saved result become `outcome_unknown`. Inspect the transcript, workspace,
and external results, then use **I reviewed the results — allow a new turn** to release the
conversation. Acknowledgement does not re-execute or certify any action.

Captured [web citations](WEB_SOURCES.md) share document citations' persistent catalog and
approval/replay behavior. Stop interrupts active built-in web fetching; a cancelled fetch
does not publish new evidence after observing cancellation. A request may already have
reached its remote server. Restart does not automatically fetch it again.

Approvals stay on the same run/accounting turn, retaining their existing 24-hour expiry,
atomic claim, exact decision validation, cumulative rounds, and current-policy checks.
Preflight rejection leaves an unclaimed approval retryable. The worker checks again after
queueing, so a policy change between acceptance and execution can still stop a resume.
Legacy approvals without a run keep their [existing contract](APPROVALS.md).

## API and replay contract

All endpoints require the current owner. Other users, including administrators, receive
404 for private run/conversation/approval IDs. No administrative content read-bypass exists.

| Endpoint | Contract |
|---|---|
| `POST /api/runs` | `ChatRequest` body; required `Idempotency-Key` header (1–100 characters). Returns run status. Reusing a key/body returns the same run; a different body returns 409. |
| `GET /api/runs?conversation_id=ID` | Latest run for an owned conversation, or null |
| `GET /api/runs/ID` | State, reason, approval/final-message IDs, cursor and acknowledgement/expiry information |
| `GET /api/runs/ID/events?after=N` | SSE replay strictly after sequence N, followed by live saved events |
| `POST /api/runs/ID/approve` | Existing `{pending_id, decisions}` body; queues resume on the same run. Retried identical decisions while queued/running do not claim twice. |
| `POST /api/runs/ID/cancel` | Stops unstarted work or acknowledges a request to stop running work; inspect returned/subsequent status |
| `POST /api/runs/ID/acknowledge` | Releases an interrupted conversation after owner review; never retries it |

`/api/chat` adapts to run creation/subscription when enabled; its optional Idempotency-Key
makes creation retries safe. `/api/chat/approve` adapts linked approvals. Existing event
payloads are preserved, with `run_id` and `seq` on persisted events and numeric SSE `id`
lines. Subscribers also receive **current** `run_state` frames; these are status snapshots,
not ordered replay events. A replayed old `done` or `paused` event does not end a resumed
subscription. The subscriber finishes when the current state leaves queued/running/stopping.
Disconnect only detaches this subscription. Compatibility clients must call the cancel API
to stop durable execution; aborting their HTTP stream is insufficient.

The browser retries a lost create response once with the same key/body. It reconnects an
interrupted event subscription with the last applied cursor (0.5–5 second backoff), ignores
duplicate sequences, and rejects callbacks from old selections. Reopening a chat rebuilds
its live view from sequence zero. Logout detaches subscriptions and clears private state;
login lets the owner reopen the run. A second tab can refresh a saved approval to see a
result completed elsewhere. Sidebar status refreshes every five seconds while enabled.

## Limits, retention, and evidence

- At most **32 unresolved runs per deployment**, **4 per user**, and **1 per conversation**.
  Waiting approvals and unacknowledged interruptions count toward these limits. Full queues
  return 429 without adding a request. Only one top-level run executes at a time.
- Request snapshots are limited to **8 MiB** of encoded JSON. Existing image/document,
  model context, tool duration, child count, and permission limits still apply.
- A replay log is limited to **2 MiB** per run, with **128 KiB** per event. Consecutive token,
  thinking, and tool-progress deltas are batched at 512 characters or after 100 ms on the
  next delta; non-text events flush the batch. Persisted sequence numbers are monotonic
  across approval resumes. Exceeding the limit stops further execution, with a durable
  reason even when no more log space is available. A tool whose result cannot be saved is
  explicitly unconfirmed. No critical action evidence is silently evicted to keep running.
- Tool preview queues hold at most **128 chunks of 8,192 characters**, plus bounded completion
  notices. Overflow drops/truncates previews with a visible omission notice; authoritative
  tool results still follow subject to the event limit. Completion cannot be dropped or
  blocked behind previews during cancellation. All tool threads are joined before release.
- `ToolExecution` records intent before dispatch and the observed result state afterward.
  Child tool calls use an inherited, thread-safe journal with distinct execution identities.
  Intent is conservative: cancellation/crash before the actual call can still leave an
  unconfirmed intent. Failed results never imply that remote effects were rolled back.
- Terminal replay content expires after **7 days** at startup/hourly cleanup. Unresolved
  approval/interruption evidence is retained until resolved. Expired cursors return 410;
  status and the final conversation transcript remain available. Acknowledged interrupted
  request snapshots expire with their replay log. Run metadata, deduplication keys, and
  tool outcome metadata remain until conversation/owner deletion. Retention is logical;
  SQLite file space is not automatically compacted.
- `Run.id` is the existing `UsageLedger.turn_id`, including compaction/child calls and
  approval resumes. Run/event recording does not bill again, reprice, or infer missing
  usage. Private run records are deleted with their owner; existing metadata-only usage
  retention is unchanged. Offline backups include run evidence; startup after restore
  uses the same conservative interruption rules.

Verification uses scripted providers, isolated SQLite/Postgres databases, native Postgres
backup/restore, and the real SPA in Chromium with synthetic HTTP fixtures. It does not
certify live providers, external side-effect reconciliation, or every managed Postgres
service. Distributed recovery, automatic safe-read retry, a dedicated run inbox, and
provider invoice reconciliation remain later work.
