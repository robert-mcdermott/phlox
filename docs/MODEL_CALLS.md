# Model-call accounting and context fit

[User Guide](USER_GUIDE.md) · [Project overview](../README.md)

Delivered in [Wave 3](IMPLEMENTATION_WAVES.md). The shared seam is
`backend/app/model_calls.py`; provider adapters only translate their wire format.

## Call lifecycle and attribution

Before each application model invocation, Phlox checks context fit and current account /
budget policy, then inserts a metadata-only `UsageLedger` row. If insertion fails, the
provider is not dispatched. Each row carries a unique call ID (`message_id`), turn ID,
optional parent call ID, actual model/profile, kind, identity snapshot, and price snapshot.
Top-level rounds, fallback, compaction, child rounds, gateway requests, connection
probes, and selected-text artifact revisions use this seam. Artifact revisions use an
`artifact_edit` kind and their own turn ID; their usage is included even when the proposal
is discarded, without changing a chat answer's receipt. Child workers use independent short DB sessions and share their
parent's turn ID; they do not share its SQLAlchemy session.

Provider usage events are cumulative snapshots, committed as soon as received. Repeated
snapshots replace counters instead of adding them. Invalid or regressing reports retain
known counters and mark usage incomplete. Streams close on normal completion, exceptions,
Stop, or consumer closure. Call status is `running`, `completed`, `failed`, `cancelled`, or
`interrupted`; usage status is independently `reported`, `partial`, or `unknown`.
A process killed before cleanup leaves a running row with unknown or partial usage.

A provider EOF without a terminal signal is now recorded as interrupted, not completed.
Provider output-limit termination can still be a completed *call* with reported usage;
it is not a completed *answer*. Message receipts separately record the task `outcome` and
bounded completion-recovery attempts. Reported reasoning tokens, when available, are a
subset of output tokens and are never added to usage or cost a second time.

Explicit OpenAI compatibility retries get separate rows linked to their preceding call.
SDK-internal transport retries are opaque: this is application-call accounting, not an
invoice-grade log of every HTTP attempt. Failed attempts without provider usage remain
unknown. Embedding calls are outside this generation-accounting seam.

Final message receipts and version-3 approval snapshots summarize the turn's rows; they
do not insert another charge. Startup backfill skips these receipts. Version-2 approval
resumes import their historical top-level counters once; historical prices and missing
child/compaction usage cannot be reconstructed. Legacy final-message rows remain intact.
Both own-user totals and admin chargeback read the same ledger as budgets, including
paused work and gateway calls. Metadata survives conversation/account deletion; prompts,
answers, arguments, and tool output are never copied into the ledger.

## Known cost, unknown cost, and cached tokens

Rates are USD per million tokens, snapshotted when the call starts. Admin Configuration
supports `input`, `output`, `cache_read`, and `cache_write`. Blank/missing rates mean
unknown; explicit zero means a zero price. Changing rates never reprices old calls.

Canonical input includes cached input. Cost uses standard input rates only for uncached
tokens and the corresponding cache rates for reported cache reads/writes. If cached
tokens occur without their rate configured, the call's cost is unknown. Cache write
pricing is a single configured rate per model; TTL-specific/provider tier pricing needs
a richer future pricing model. OpenAI adapters read prompt token details. Bedrock input
adds ordinary input and cache read/write counters, following the
[AWS token fields](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_TokenUsage.html)
and [AWS invocation-token accounting](https://aws.amazon.com/blogs/aws/reduce-costs-and-latency-with-amazon-bedrock-intelligent-prompt-routing-and-prompt-caching-preview/).

Usage APIs expose nullable `cost_usd`, `known_cost_usd`, `unknown_usage_calls`,
`unknown_cost_calls`, `calls`, and distinct `turns`. A partial report contributes known
tokens/cost but never becomes a complete total. UI receipts, chargeback, and CSV retain
this distinction. Historical rows count as legacy entries; they cannot be decomposed into
individual calls. Gateway wire usage keeps the OpenAI-compatible shape, while its ledger
retains missing/partial status. Budgets sum known cost and gate subsequent calls; they do
not reserve funds, estimate unreported bills, or stop an already running model stream.
Parallel calls can overshoot. See [BUDGETS.md](BUDGETS.md).

## Context fit

Every generation request passes a final check, including compaction and gateway requests.
The effective window is the smaller of `max_context_tokens` and optional profile
`context_window` (set through YAML or the profiles API; preserved by UI profile edits).
The checker reserves `max_tokens` for output, counts serialized instructions/history,
tool arguments and schemas, and estimates images at 4096 tokens each. Text uses UTF-8
bytes / 3 plus framing overhead. This is a heuristic, not a provider tokenizer guarantee;
configure headroom, especially for images and unusual tokenizers.

Oversized tool results are shortened only in a copied provider request, with a visible
status and truncation marker. Saved tool output, user/system messages, schemas, and
call/result IDs remain intact. If that still cannot fit, the call fails before dispatch
with an actionable context-limit error; fallback does not bypass it. Compaction has its
own bounded prompt. Long-history compaction remains a separate earlier heuristic and
does not promise to summarize every oversized request automatically.

## Effective settings and completion recovery

New turns, including turns in existing chats and regeneration, resolve current runtime
generation settings, then assistant overrides, then explicit conversation overrides. The
same resolved context window (also bounded by the profile) drives pre-run compaction and
the final fit check. A truncated compaction summary never replaces the original history.
Queued durable runs resolve settings when they begin execution. In-flight turns retain
their prepared parameters. Approval resumes retain saved allowances and apply stricter
current user/assistant/conversation output, context and round limits; they cannot extend a
saved allowance or reset cumulative rounds.

Old `Conversation.params` values are historical creation snapshots, not persistent
generation overrides. This fixes old chats retaining a stale round limit when the user
changes Settings. API clients can explicitly override generation values through
`PATCH /api/conversations/{id}` with `params`; these overrides are now marked separately
and apply consistently to output, context, temperature and rounds. Clients relying on an
older unmarked custom value should submit that override again. `params: null` clears the
generation overrides. Conversation model/profile selection keeps its existing behavior.

Ordinary agent tasks with more than one allowed pass reserve their last pass for a final
answer without tools. Unfinished ordinary text or Research synthesis can continue from
the retained answer and existing evidence with up to two additional **tool-free** calls,
only when room remains under the effective Max tool rounds setting. Recovery does not
increase per-call output/context limits, bypass budget checks or ignore Stop. It never
executes truncated tool requests, retries uncertain actions or restarts gathering. Repeated
empty/no-progress output, unavailable context, exhausted rounds and provider/policy failures
leave a clear incomplete outcome with saved progress. Automatic continuation does not
guarantee semantic completeness; artifact/workflow verification remains Wave 14 work.
Automatic continuation is disabled when output guardrail rules are active: joining two
separately checked streams could reconstruct sensitive text at their boundary. The partial
answer remains explicitly incomplete and can be continued in a separate Chat turn.

For new calls, `UsageLedger.usage_details.call` stores metadata from the model-call seam:
effective context/output/round limits, configured profile context cap, original/fitted input
estimates, trimming, stage, setting origin and provider finish reason. No prompt, tool body
or secret is stored there. `runtime` means merged user settings/deployment defaults; it
does not distinguish those two origins. Adapter/SDK-internal behavior remains outside this
record. Under an answer, open **Context record → Model calls** to inspect linked call
diagnostics and reported reasoning usage. Legacy calls without this metadata stay unknown;
the records do not reconstruct their historic limits. Private context access still requires
conversation ownership, including for admins.

## Upgrade and verification

Wave 14 diagnostics use existing JSON metadata; no schema migration is required. Wave 3
introduced nullable ledger columns through the old additive upgrade. Wave 4 now
uses a checked Alembic baseline and preserves existing rows; see
[BACKUP_RESTORE.md](BACKUP_RESTORE.md). Worker recovery, call-level admin inspection UI,
and provider invoice reconciliation remain later work.

[Accounting tests](../backend/tests/test_model_calls.py) cover concurrent children,
fallback/retries, interrupted usage, prices/cache rates, context limits, deletion/backfill,
and schema upgrades. [Approval tests](../backend/tests/test_approval_continuity.py) cover
version-2 import and version-3 reconciliation. [Browser tests](../frontend/tests/browser/approval.test.js)
verify unknown-cost receipts, chargeback and CSV using synthetic API fixtures.
