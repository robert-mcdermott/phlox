# Phlox improvement roadmap

Reviewed **2026-09-07** against commit `e07ddc7d`. This is the active product and
engineering plan. The [codebase review](CODEBASE_REVIEW.md) records evidence, limitations,
and verification; the [original roadmap](ROADMAP_LEGACY.md) preserves the delivery history.
**M1 and M2 have delivered increments:** [Waves 1–9](IMPLEMENTATION_WAVES.md) deliver
foundations, document/web evidence, and opt-in bounded Research with admin-managed search.
The wave log records verification and remaining limits. M3–M5 remain proposed. Existing features and
completed work are identified explicitly; unchecked entries do not yet ship.

## 1. Product direction

**Make Phlox the self-hosted workspace where people research a question, produce useful
work, and return later to continue—with control over their models, data, and agent actions.**

Phlox already has the core ingredients: streaming, tools, document search, memory,
assistants, skills, artifacts, multiple providers, and isolation options. The next leap
comes from making these ingredients work together reliably. Keep FastAPI, React, the
provider-neutral harness, unified registry, and sandbox interfaces. Evolve them in small
releases; a framework rewrite is not a prerequisite.

Initial audience: a technically capable individual or small team running its own instance
for research, writing, analysis, and lightweight software work. Design for a maintainer
who also operates the deployment. Shared use must preserve strict ownership boundaries;
regulated/sensitive-data deployment remains a separate, explicitly gated track.

### Three experiences to make excellent

1. **Answer and research:** ask a question across selected documents and the web, inspect
   the supporting passages, resolve conflicting evidence, and save a useful brief.
2. **Create and revise:** produce a document, analysis, or small app; inspect it alongside
   the conversation, edit a section, compare versions, and export a usable result.
3. **Continue a project:** come back days later, see decisions and open work, reuse the
   right sources, and continue a task without reconstructing its context.

The first integrated demo should be: create a project, add three documents, research a
question, open each citation, turn the answer into a brief, revise it, close the browser,
and return to the same work. Model choice and permissions must remain understandable
throughout that journey.

### Reference products: patterns worth adapting

These are narrowly sourced product patterns, not an exhaustive feature or quality ranking.
Official documentation was consulted for this review; availability and plan limits change.

| Reference | Useful pattern | Phlox implication |
|---|---|---|
| ChatGPT | Projects bring related chats, instructions, files, and sources together. [Official projects guide](https://learn.chatgpt.com/docs/projects) | Introduce a persistent project above conversations; preserve context without replaying every project chat. |
| Claude | Projects organize knowledge and instructions; artifacts provide a separate place to build and revise substantial output, including interactive components. [Projects](https://support.claude.com/en/articles/9517075-what-are-projects), [artifacts](https://support.claude.com/en/articles/9487310-what-are-artifacts-and-how-do-i-use-them) | Promote the existing canvas into an editable, versioned deliverable surface. |
| Perplexity | Research mode iterates through searching, reading, and synthesis; persistent workspaces organize ongoing research. [Research](https://www.perplexity.ai/help-center/en/articles/10738684-what-is-research-mode), [workspaces](https://www.perplexity.ai/help-center/en/articles/10352961-what-are-spaces) | Make evidence, source selection, research progress, and unanswered questions visible. |

Phlox's proposed distinction is the combination of self-hosting, portable work, explicit
data destinations, inspectable evidence, and controlled execution. These are product bets
to validate, not claims of features unique to Phlox or parity with commercial model quality.

## 2. What to preserve and what to change

| Preserve | Improve |
|---|---|
| Provider-neutral canonical messages and tool loop | Explicit capability contracts, consistent execution context, model-call accounting |
| One registry for built-ins and MCP | Validated arguments, bounded execution, effective permissions inherited by children |
| Persisted approval snapshots | Durable runs, recoverable approvals, atomic transitions, explicit cancellation |
| SQLite default; optional Postgres | Versioned migrations, tested upgrades and restores, truthful scale guidance |
| Qdrant abstraction and hybrid retrieval | Embedding identity, robust ingestion, source locations, citation quality |
| Existing skills and assistants | Project context, portable versioned workflows, clearer discovery |
| Canvas, workspace files, checkpoints | Editable artifacts, branch preservation, bounded previews, user-visible versions |
| Strict per-user access and fail-closed execution isolation | Explicit egress controls, scoped integrations, resource quotas, operational verification |

Priority order: **fix correctness → ship trustworthy research → connect projects and
deliverables → enable bounded background work → polish distribution and extensions**.
The old gateway Phase 2 is deferred until the run and policy foundations exist.

## 3. Release sequence

Effort ranges below are planning estimates for **one experienced full-time maintainer**,
including tests and documentation, with no major rewrite. They are not delivery dates.
External-provider compatibility, migration edge cases, and part-time availability can
expand them materially. Re-estimate after M1; fund one milestone at a time.

| Milestone | Outcome | Indicative effort | Dependencies | Release demonstration |
|---|---|---|---|---|
| **M1 — Dependable foundation** | Recoverable work, correct policy/accounting, tested upgrades | 6–10 weeks | None | Refresh or switch chats during a run; recover an approval; totals reconcile across a child, resume, and fallback |
| **M2 — Trustworthy research** | Source-backed answers and bounded research with measurable retrieval quality | 3–5 weeks | M1 execution/persistence; source prototype can start earlier | Compare three documents and web sources; every citation opens the actual passage; report disagreement and missing evidence |
| **M3 — Projects and deliverables** | Context across chats, preserved branches, editable versioned output | 4–6 weeks | M1 migrations; M2 sources | Resume a project after a week, revise a brief in canvas, compare versions, export |
| **M4 — Controlled autonomy** | Background tasks, change monitors, reusable workflows, scoped integrations | 4–6 weeks | M1 run/policy/accounting; M2 evidence; M3 project model | Monitor an approved source, report a meaningful change, stop at an unapproved external action |
| **M5 — Self-hosted release** | Proven installation, privacy modes, accessibility, compatibility, recovery | 3–5 weeks | Integrated M1–M4; UX/operations work starts in M1 | A new operator installs, configures a local model, completes the core journey, backs up and restores it |

Ship small increments inside each milestone. Include a user-visible improvement in M1
(recoverable approvals, clearer run states, scroll/draft behavior), and prototype source
cards alongside the persistence work. Avoid a long stretch of backend-only changes.

## 4. M1 — Dependable foundation

### M1.1 — Close the concrete correctness gaps first

- [x] Fix MCP reconnect locking and cover reconnect, failed initialization, timeout,
  cancellation, and stale-session cleanup. Implemented in [Wave 1](IMPLEMENTATION_WAVES.md)
  with loop-owned sessions, tracked calls, and cleanup before replacement. Review **R4**
  describes the original defect.
- [x] Pass the resolved parent profile/model, generation parameters, allowed tools, owner,
  verified assistant scope, approval mode, and cancellation into each child. Wave 1 removes
  global-settings fallback, bounds read-only workers, and sequences mutating children
  within the parent turn. See the original finding **R3** and Wave 1's remaining boundaries.
- [ ] Extend inherited context with parent run IDs and project policy when those entities
  land; enforce deployment-wide concurrency and ownership of active conversation runs.
- [x] Persist consumed rounds, usage, effective settings, and terminal status across
  pauses. Consume approvals atomically; reject duplicate decisions; enforce expiry and
  current account/tool/budget policy at resume. Expose pending approvals after reload.
  Implemented in Wave 2; see [approval semantics and limits](APPROVALS.md). Durable
  worker/crash reconciliation remains below. See **R1–R2**.
- [x] Count every application generation invocation, including compaction, children, retries when usage is
  available, fallback, and paused work. Price each invocation using its actual model and
  a rate snapshot. Label unavailable usage/pricing explicitly; do not equate unknown with
  free. Reconcile to the existing metadata-only usage ledger without double-counting.
  Implemented in Wave 3; [scope and limits](MODEL_CALLS.md) include opaque SDK retries
  and embeddings outside this seam.
- [x] Add a last-resort context fit check before each model round, including tool schemas,
  tool output, image estimates, and reserved output tokens. Bound a single oversized turn;
  compaction must not silently return an over-budget request. Wave 3 uses a bounded
  heuristic with configurable profile caps; exact provider tokenizers remain future work. See **R5**.
- [x] Validate tool arguments against their schemas before dispatch. Return actionable
  errors for malformed calls; do not convert invalid JSON into a valid-looking empty call.
  Resolve missing preferences from the registered tool's default policy, and reject unknown
  tools. The permission-default/unknown-tool portion is complete in Wave 1; general schema
  validation and malformed-call handling are delivered in Wave 9.
- [ ] Correct misleading operational and extension documentation. Several obvious stale
  passages are corrected with this plan; verify the remaining guides against actual startup.

**Acceptance:** deterministic regressions cover two different users' child settings,
parent-disabled tools, resume policy changes, double approval submission, cumulative round
limits, mixed-model costs, MCP reconnect, and oversized short histories. Existing tests
stay green. A failed or interrupted run is visibly distinct from successful completion.

### M1.2 — Make a run independent of its HTTP connection

Wave 5 delivers `Run`, `RunEvent`, and `ToolExecution` records behind an opt-in flag; see
[RUNS.md](RUNS.md). The broader target below also includes future leases/inbox/retry work.

Add `Run`, `RunEvent`, and `ToolExecution` records. A conversation contains messages;
a run is one attempt to produce an outcome. Store a versioned snapshot of its execution
context, stable tool-call IDs, monotonic event sequence, partial output, heartbeat,
parent run, and terminal reason.

Proposed states: `queued → running → awaiting_approval → running → succeeded`, with
explicit `failed`, `cancelled`, and `interrupted` paths. A lease identifies the executing
worker; an expired lease triggers recovery, not blind replay of side effects.

- [x] Separate run creation, event subscription, approval, and cancellation endpoints.
  Keep `/api/chat` working through an adapter during migration.
- [x] Use a database-backed queue and a bounded worker in the existing single-process
  deployment first. A broker or separate worker fleet is optional later.
- [x] Persist events before exposing them. Reconnect with a cursor; deduplicate in the UI.
  SSE is sufficient for replay; WebSockets are not required to detach work from a tab.
- [ ] Switching chats, refreshing, or losing the network detaches the subscriber. Only
  explicit Stop requests cancellation. **Wave 5 delivers this with sidebar/status recovery;**
  a dedicated runs/approvals inbox remains proposed.
- [ ] Persist side-effect intent and result. Retry safe reads; require reconciliation or
  confirmation when a worker dies after an external action with an unknown result.
  **Wave 5 records intent/results and requires review; automatic read retries remain deferred.**
  Exactly-once external execution cannot be promised without downstream idempotency.
- [ ] Limit concurrent runs per user/deployment, tool duration, output bytes, and child
  count. Backpressure must bound progress queues and event storage. **Wave 5 bounds admission,
  previews, and saved events; inherited tool timeouts/child limits still apply.**

**Acceptance:** disconnect/reconnect, two tabs, server restart at an approval, and worker
failure around a mutating call produce no duplicate persisted result or automatic duplicate
action. The UI recovers partial work and tells the user whether a run resumed or needs
attention. Stop acknowledgement and actual provider/tool termination are reported separately.

### M1.3 — Make changes safe to ship

- [x] Replace `_ensure_columns` with Alembic migrations and a baseline for existing
  SQLite/Postgres installations. Verify schema shape before stamping old databases.
  Delivered in Wave 4; [migration and recovery guide](BACKUP_RESTORE.md).
- [ ] Add isolated browser tests for login/setup, send/stream, approval after reload,
  stop/reconnect, conversation switching, attachment, and canvas. Use scripted providers;
  no paid model calls in ordinary CI.
- [x] Add a documented backup/restore command covering database, source uploads, attachments,
  workspaces/checkpoints, config/DB overlays, and secrets references. Quiesce writes or use
  coordinated snapshots. Qdrant may be rebuilt from the authoritative data.
- [x] Add CI checks for migrations on both databases and deployment lifespan/readiness.
  Keep one process as the supported default; Postgres alone does not make caches, queues,
  MCP sessions, rate limits, or workspace locks distributed.
- [ ] Split frontend run state from navigation/auth/settings incrementally; type the run
  events and API boundary first. Add lint and behavior tests before wider TypeScript work.
  Reduce the chat router by extracting context assembly and run services as they change.

**Acceptance:** upgrade a populated prior-version fixture, restore its backup into a clean
instance, and recover chats, documents, files, permissions, and usage. Failed migrations
stop startup with actionable guidance. CI exercises the browser journey, not just bundling.

## 5. M2 — Trustworthy research

### M2.1 — Make citations real data

**Wave 6 delivers the document foundation:** a private source registry, conversation-stable
S-labels, typed message references, and clickable retained excerpts for uploaded documents
and assistant knowledge bases. **Wave 7 adds richer document provenance; Wave 8 extends
capture to web pages**, including failed fetches, DNS-pinned bounded transport, and source
removal. See [SOURCES.md](SOURCES.md) and [WEB_SOURCES.md](WEB_SOURCES.md). Artifact-version
citations remain below; historical answers are not retroactively converted.

Extend the source registry with web evidence and richer ingestion locations.
Store document/version, page or section and offsets, passage, retrieval query, URL/title,
fetch time, and content hash as applicable. Distinguish discovery snippets from fetched
evidence. Give citations typed message metadata instead of relying on Markdown parsing.

- [x] Render inline citation chips and a source drawer with the exact supporting passage;
  support both uploaded files and fetched pages. Deduplicate sources without losing passages.
  **Documents delivered in Wave 6; fetched HTML/text pages delivered in Wave 8.**
- [x] Validate document citation IDs and chunk-relative evidence locations. Mark unsupported references and omitted
  evidence; a valid source ID alone does not prove that a claim is supported.
  **Wave 6 marks unknown labels and omitted/truncated passages; semantic claim verification remains later.**
- [ ] Preserve citations in exports and artifact versions. Source access must be rechecked
  on every read. Deletion removes retained content or leaves an explicit unavailable marker.
  **Waves 6/8 deliver document/web Markdown export and access/deletion checks; artifact versions remain.**
- [x] Keep source snapshots private, bounded, and subject to retention policy. Record
  fetch failures/paywalls instead of inventing source content.
  **Waves 6/8 apply shared snapshot bounds and 30-day expiry to documents/web.** Web failures
  have no supporting excerpt; HTTP-200 access-barrier detection is explicitly heuristic.

**Acceptance:** repeated searches cannot make `[1]` refer to different documents; all
citations in the fixture suite resolve to the intended accessible passage. A second user
cannot open a private source by copying its URL or ID.

### M2.2 — Improve ingestion and retrieval before adding more retrieval machinery

- [x] Record embedding provider/model/version/dimensions and parser/chunker version.
  A same-dimension model change must still trigger a controlled rebuild. Never mix remote
  and hash embeddings silently after an outage. Offer an explicit lexical-only degraded mode.
  **Delivered in Wave 7**, including explicit staged rebuilds and legacy identity detection.
- [ ] Use durable, retryable ingestion jobs with progress, content hashes, deduplication,
  cancellation, size/page/time quotas, and reconciliation between database and Qdrant.
  **Wave 7 delivers** persisted attempts/progress, explicit retry after restart, content
  hashes, replacement without duplicate SQL chunks, bounded processing, and staged rebuilds.
  Cross-upload deduplication, a separate cancel action, hard parser isolation, and staging
  collection garbage collection remain; current deletion/shutdown checks are cooperative.
- [ ] Preserve PDF page boundaries, headings, and DOCX tables. Add explicit unsupported-file
  errors. Offer OCR for scanned PDFs as an optional worker with visible resource costs.
  **Wave 7 delivers** PDF pages, Markdown/DOCX headings, DOCX tables, and format errors.
  PDF layout/heading inference and optional OCR remain.
- [ ] Add a tested local semantic embedding option; retain the dependency-light lexical
  path. Benchmark a cross-encoder behind the existing reranker interface before making it
  a default dependency. Add section-aware chunks and neighboring context where measured useful.
- [x] Treat index/provider errors differently from zero relevant results. Revalidate source
  ownership/deletion against authoritative records when serving retrieval results.
  **Delivered in Wave 7**, including rechecking assistant visibility after embedding calls.
- [ ] Build a versioned retrieval set with exact facts, paraphrases, tables, conflicting
  versions, no-answer questions, and cross-user distractors. See release targets below.
  **Wave 7 adds** versioned keyword fixtures, real PDF/DOCX parsing checks, a scripted
  paraphrase provider, and a measured table-extraction improvement over the old parser.
  Representative live-model evaluation and release-quality retrieval scores remain.

See [INGESTION.md](INGESTION.md) and [Wave 7](IMPLEMENTATION_WAVES.md#wave-7--reliable-document-ingestion-and-richer-source-locations)
for shipped limits, upgrade steps, verification, and remaining work.

**Acceptance:** model migration/outage tests leave the last good index usable, interrupted
ingestion is recoverable, deleted/private documents cannot surface, and the retrieval
benchmark improves over the measured current baseline.

### M2.3 — Offer clear Quick answer and Research choices

Keep advanced controls available, but organize the composer around intent. Research
selects sources and a time/token/cost budget; the agent proposes a short plan, gathers
evidence, identifies gaps or disagreements, and writes a cited answer. A document-only
scope must be a server-enforced constraint, including child tools and fallbacks.

- [x] Implement bounded search/read/synthesize passes through the existing harness.
  **Delivered in Wave 9:** explicit opt-in mode, planning, bounded gathering, reserved
  synthesis, selected-document scope, domain restrictions, and DDG/Serper/SearXNG search.
  The seeded `deep-research` skill is useful guidance, not a substitute for this workflow.
- [ ] Add source filters (selected documents, domains, date range), stronger page extraction,
  duplicate detection, fetch byte limits, and cancellation. Harden the fetch connection
  against DNS changes between validation and connection before increasing fetch concurrency.
  **Wave 8 delivers** bounded HTML/text extraction, duplicate passage identities, body/time
  limits, cancellation, and numeric-address pinning with HTTPS hostname verification.
  Wave 9 adds document/domain scope and sequential orchestration; date-range filtering remains.
- [ ] Show progress as sources found/read, unresolved questions, and remaining budget.
  **Wave 9 delivers** stages, a concise plan, source/search/read counts and available
  usage; unanswered questions belong in the report, not a separate tracked task model.
  Show a concise work log, not a claim to expose the model's hidden reasoning.
- [ ] Preserve evidence versus inference and identify disagreements with dated sources.
  **Wave 9 provides** report instructions, inspectable citations, and partial-result
  handling; semantic claim verification and live-model quality targets remain open.
  End with a useful partial result when the budget expires.

**Acceptance:** a research run stays inside source and budget limits, produces a useful
partial result on failure, and supports inspection of both agreement and contradictory
evidence. Benchmark grounding, usefulness, latency, and cost against Quick answer.

## 6. M3 — Projects and deliverables

### M3.1 — Introduce projects and inspectable context

- [ ] Add a private `Project` above conversations, with instructions, linked documents,
  pinned decisions, open tasks, artifacts, and default provider/data policy. Keep chats
  without a project supported. An assistant remains a reusable persona; a skill remains
  a workflow; neither substitutes for a project.
- [ ] Add a project overview: current brief, recent outputs, unresolved questions, and
  active runs. Link every extracted decision/task to its originating message/source.
- [ ] Add a context inspector explaining which instructions, memories, and sources are
  being used, why, and where they will be sent. Let users exclude material before a run.
- [ ] Make memory editable and scoped (personal/project), with provenance, review state,
  correction, expiry, and a no-memory/temporary-chat option with defined retention.
- [ ] Use a bounded context assembler; project membership does not imply replaying every
  chat. Precedence: deployment policy → project limits → assistant limits → run choices;
  permission layers intersect. Keep instruction precedence separate from access control.
- [ ] Add project/chat archive, pinning, pagination, and full-text message search.
  Defer collaborative sharing until membership/revocation tests cover every data surface.

**Acceptance:** two chats share selected project knowledge while unrelated projects remain
isolated. A corrected memory is not reintroduced from a stale retrieval index or summary.
Users can identify and remove the context behind an answer.

### M3.2 — Preserve conversation alternatives

- [ ] Replace destructive edit/regenerate truncation with immutable message ancestry and
  an active branch pointer. Keep branch navigation simple: previous/next alternative first.
- [ ] Bind runs and artifacts to a branch/version. Fork from a workspace checkpoint or
  declare that file state is shared; do not imply that switching message branches undoes files.
- [ ] Add optional side-by-side model comparison with explicit extra cost and a chosen
  result. It must obey the same data policy and not mutate a shared workspace concurrently.

**Acceptance:** editing an old prompt preserves the original answer, its citations, files,
and usage. A failed regeneration leaves the old version usable.

### M3.3 — Turn canvas into a workspace for finished output

- [ ] Add `Artifact` and `ArtifactVersion` metadata: source run, path, media type, hash,
  title, provenance, and version. Keep files in the workspace/object-storage seam.
- [ ] Support document editing, selected-section instructions, version diffs, and revert.
  Detect concurrent user/agent edits instead of silently replacing them.
- [ ] Add CSV/table inspection and PDF preview; retain direct downloads. Ship a small
  curated set of document/report/data-analysis skills with output validation and templates.
- [ ] Add a React/JSX preview only through an isolated, pinned build environment, with
  preview CSP/network policy, resource limits, and no app credentials. Existing opaque
  iframe isolation protects parent access but does not by itself prevent outbound traffic.
- [ ] Offer portable exports: brief with citations, artifact files, and a project manifest.
  Publishing or sharing is a separate explicit action with a clear access policy.

**Acceptance:** revise one paragraph without losing the rest; compare and restore versions;
download a self-contained result. Preview tests cover parent isolation, unintended network
egress, malicious content, large files, and narrow/mobile layouts.

## 7. M4 — Controlled autonomy

### M4.1 — Background tasks and change monitoring

- [ ] Add user-created scheduled runs with timezone, next-run preview, maximum runtime,
  spend limit, allowed sources/actions, pause/delete, overlap policy, and deduplication.
- [ ] Recheck current ownership, credentials, tool policy, and budgets at execution time.
  Persist an unattended approval in the inbox; never silently broaden permissions.
- [ ] Add an in-app completion/attention inbox. External notifications require opt-in.
  Notify on meaningful change, failure, or required action; unchanged monitors stay quiet.
- [ ] First monitor: compare an approved source version with the previous one, show the
  passages that changed, and identify which saved conclusions may need review.

**Acceptance:** restart/timezone/overlap tests produce one intended run per occurrence;
revoked access stops future reads; paused schedules do not execute; duplicate events do
not duplicate notifications. Monitoring has a visible storage and cost budget.

### M4.2 — Reusable workflows and trustworthy connections

- [ ] Extend skills into optional versioned workflow packages: instructions, resource
  files, input schema, output contract, and test examples. Import previews requested
  capabilities and provenance; installing a workflow does not grant execution privileges.
- [ ] Let users save a successful run as a draft recipe, replace example values with
  parameters, review its actions, and test it before scheduling. Replay reruns the workflow;
  it does not promise deterministic model output.
- [ ] Add per-user connection ownership and credential references; keep deployment-shared
  MCP distinct and explicit. Implement delegated OAuth only for selected integrations.
- [ ] Improve MCP lifecycle, rich content handling, health, cancellation, and tool discovery.
  Begin with one real read-only integration and one draft-producing action, chosen through
  pilot use. Require a separate permission for sending/publishing.

**Acceptance:** user A's connection cannot be invoked by user B, secrets never enter prompts
or exports, a workflow update cannot silently gain permissions, and a draft recipe produces
the declared artifact under a bounded test run.

### M4.3 — Revisit the agent API after these foundations

- [ ] Expose the same run service through a documented versioned API with create/status/
  events/approve/cancel, scoped API keys, rate limits, and per-key budgets if needed.
- [ ] Decide whether `/v1/agent/completions` is a convenience adapter or a versioned
  `/api/runs` client is the better contract. Preserve existing raw `/v1/chat/completions`.
  Do not duplicate agent policy or execution inside the gateway router.
- [ ] Publish a compatibility matrix for the raw gateway; reject unsupported meaningful
  fields explicitly instead of promising universal SDK compatibility.

**Acceptance:** UI and API create equivalent runs with identical ownership, policy, costs,
and terminal states. Key scopes are enforced, not just stored metadata.

## 8. M5 — A self-hosted product people can operate

This work starts incrementally in M1; M5 is the integrated release gate.

- [ ] First-run setup: provider selection, actual capability probe, embedding choice,
  sandbox/readiness check, and a successful sample task. Explain configuration provenance
  (file seed versus live override) in diagnostics; protect secrets throughout.
- [ ] Publish tested deployment profiles: local/trusted development, isolated single-host
  shared use, and optional remote services. Preserve an entirely local inference/search
  option. Provide a credible local isolated-execution path for the packaged app without
  casually handing its process unrestricted access to the host container engine.
- [ ] Add provider capability metadata/probes for context/output limits, tools+vision,
  structured output, reasoning controls, usage, and cache usage. Keep compatibility work
  in adapters. Prioritize native APIs only when real user tasks require their features.
- [ ] Implement **Local only / Approved providers / Ask before cloud** data policies.
  Apply them to chat, embeddings, summaries, children, fallback, search, MCP, telemetry,
  and artifact previews—not just the main model selector. A label without enforcement
  must not claim that data stays local.
- [ ] Add keyboard navigation, focus management, accessible dialogs and approvals,
  touch/mobile canvas, screen-reader announcements, draft recovery, and scroll anchoring.
  Fix hard-coded status colors through semantic tokens. Measure long-thread rendering
  before adding virtualization; throttle streaming renders where needed.
- [ ] Test locked dependency upgrades and release images; resolve deprecations in small
  batches. Publish versioned release notes, migration/restore instructions, support matrix,
  resource guidance, and a redacted diagnostic bundle.
- [ ] Evaluate voice as optional push-to-talk plus read-aloud with pluggable local/remote
  providers and clear microphone state. Full real-time voice waits for measured demand.

**Acceptance:** a new operator reaches a useful result without reading implementation
code; the local-only fixture makes no external calls; backup/restore is rehearsed; core
journeys work with keyboard and touch; supported provider/deployment combinations pass
their declared tests. No claims of PHI readiness arise from this milestone.

## 9. Phlox-specific experiments

Prioritize **Evidence trail** and **Context inspector** first because they directly improve
the three core journeys. The rest are conditional bets; measure use before expanding them.

| Experiment | First useful version | Prerequisites | Continue only if… |
|---|---|---|---|
| **Evidence trail** | An answer/brief links claims to passages, source versions, and the run that assembled it; show disagreement and unavailable evidence | M2 source registry | Pilot users can verify an answer faster and cite it accurately |
| **Context inspector** | “Used these memories and sources”; exclude or correct one and rerun | M3 scoped context | Users resolve incorrect-context answers without editing a system prompt |
| **Run receipt** | Exportable record of models, inputs by reference, tool actions, approvals, artifact hashes, and known/unknown costs | M1 events/accounting; M3 versions | Users can explain what happened and safely repeat a workflow |
| **Change-aware briefs** | Refresh a saved report and highlight changed evidence and affected conclusions | M2 versioned sources; M4 schedules | Alerts are useful enough to keep enabled; unchanged checks remain quiet |
| **Private model escalation** | Try an approved local model; show the proposed cloud destination/context before escalation when policy requires it | Capability profiles; end-to-end egress policy | Measured quality improves within the user's latency/cost/privacy constraints |
| **Run-to-recipe** | Convert a successful project task into a reviewed, parameterized skill/workflow | M3 artifacts; M4 packages | A second run saves effort and produces a validated deliverable |

## 10. Quality targets and measurement

These are **proposed release targets**, not current measurements. The initial review
baseline was 179 passing backend tests; Wave 4 reaches 319 passing tests (including
SQLite/Postgres recovery drills), with one non-applicable case skipped and six browser scenarios
and a successful build.
Live answer quality and browser performance have not been measured. Use synthetic fixtures and opt-in, locally
stored pilot feedback; raw user content must not become default telemetry or eval data.

| Measure | Definition and initial target |
|---|---|
| Core task success | Freeze at least 20 representative tasks across the three journeys; at least 85% pass the rubric on each advertised capable provider profile, over three runs each. Publish results per profile, hardware, and date. |
| Evidence quality | At least 50 answerable/no-answer retrieval questions; Recall@5 ≥ 85% on answerable cases. All generated citation IDs resolve; ≥ 90% of sampled factual claims are supported under human review. Report abstention separately. |
| Policy correctness | All deterministic cross-user, child-scope, resume, source-deletion, and egress regression tests pass; any demonstrated isolation regression blocks release. |
| Recovery | All scripted refresh, disconnect, restart, duplicate-approval, and uncertain-side-effect scenarios reach the specified recoverable state without automatic duplicate actions. |
| Cost integrity | Fixture totals reconcile exactly across calls, children, pause/resume, and fallback; unknown provider usage remains labeled unknown. Compare estimated cost with actual provider records in a limited live check. |
| App responsiveness | Initial target: p95 run acknowledgement < 500 ms and replayed state visible < 2 s on a declared local test setup. Measure model first-token latency separately. Core controls remain responsive in a 500-message fixture. |
| Setup and restore | At least 4 of 5 fresh-install pilot users complete setup and one useful task in 15 minutes, excluding downloads. Every supported deployment passes a documented restore drill. |
| Product usefulness | Weekly maintainer/pilot review: was the artifact used, could the answer be verified, and was the project revisited? Establish a baseline before setting retention targets. |

Add provider contract fixtures and outcome-based assertions to the existing eval harness.
Its three current scenarios mostly check tool/file presence. Expand to answer correctness,
citation support, artifact validity, permission behavior, recovery, latency, and total cost.
Run small, explicitly configured live smoke tests before a release; keep CI offline.

## 11. First implementation backlog

Start here; do not open every milestone simultaneously. Sizes are relative:
**S** ≤ 2 days, **M** 3–5 days, **L** roughly 1–2 weeks, subject to investigation.

| Order / ID | Work item | Size | Completion evidence |
|---|---|---|---|
| 1 / F01 | MCP reconnect and cancellation cleanup | S–M | **Complete, Wave 1:** reconnect, failed init, cancellation, slow teardown, shutdown, and real stdio tests |
| 2 / F02 | Parent execution-context inheritance and child concurrency limit | M | **Complete, Wave 1:** two-user/fallback models, scope inheritance, bounded read-only fan-out, sequential mutations |
| 3 / F03 | Resume state/accounting, atomic approval claim, cumulative limits | M | **Complete, Wave 2:** cumulative counters, atomic claim, current policy, expiry, recovery, terminal outcomes |
| 4 / F04 | Per-model-call usage records and context fit enforcement | L | **Complete, Wave 3:** call attribution, partial usage, price snapshots, approval reconciliation, bounded context |
| 5 / F05 | Browser test harness and state isolation | M | **Started, Waves 2–3:** isolated Chromium approval/accounting scenarios in CI; extend to full-stack and further user journeys |
| 6 / F06 | Migration baseline plus backup/restore fixture | L | **Complete, Wave 4:** checked migrations, offline bundles, SQLite/Postgres restore with checkpoint/index recovery |
| 7 / F07 | Durable run/event service plus reconnect UI, behind a flag | L, then re-estimate | **Delivered, Wave 5 (opt-in):** one bounded worker, persistent progress, reconnect/Stop UI, approval and conservative interruption recovery; see [RUNS.md](RUNS.md) |
| 8 / F08 | Source registry and clickable citations prototype | M | **Delivered, Wave 6 (documents):** stable references across direct refs/searches, source panel, private snapshots, recovery and Markdown exports; see [SOURCES.md](SOURCES.md) |
| 9 / F09 | Embedding identity and ingestion consistency | M–L | Same-dimension model change, outage, deletion, and retry fixtures pass |
| 10 / F10 | First five core-journey evals and pilot scripts | M | Explicit pass rubrics, provider metadata, costs, and failure examples recorded |

F03 is a minimal correctness repair; F07 moves that state into the durable run service.
F04 supplies call IDs that can attach to runs. F06 is complete and precedes shipping new persistent
schemas; F08 may prototype on fixtures while F07 lands. Expand F10 to the release targets
as M2/M3 ship. This ordering deliberately pairs foundational work with visible progress.

## 12. Deferred scope and review cadence

Defer until the core journeys justify them: general desktop/browser control, autonomous
external publishing, a public plugin marketplace, unrestricted multi-agent swarms,
multi-region/Kubernetes operation, full real-time voice, and exhaustive provider parity.
Avoid mandatory Redis, GPU services, or heavyweight rerankers in the default installation.
External connectors and broad sharing need a demonstrated pilot use case.

Sensitive-data/PHI use remains gated on a separate threat model, applicable provider and
organizational requirements, secrets management, encryption, retention/deletion policy,
content-free administrative audit, and an independent security review. Optional Postgres
is already implemented; it is not the remaining governance work.

At the end of each milestone, record the demo, test/eval results, migration notes, pilot
feedback, and newly discovered risks. Mark an item complete only with its acceptance
evidence and updated architecture/extension docs. Reorder the next milestone based on
observed use; preserve stable backlog IDs and archive superseded decisions.
