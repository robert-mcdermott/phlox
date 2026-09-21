# Research mode

Research is an explicit choice in the composer. **Chat remains the default**, including
new conversations and the next turn after sending a research request. Research does not
replace ordinary chat, the Web search checkbox, Agent mode, or skills.

## Start a research task

1. Change **Chat** to **Research** in the toolbar below the message field.
2. Choose **Web**, **Selected documents**, or **Documents + web**. For document research,
   select at least one ready document from the list; you can attach and process a new file.
3. Choose **Brief**, **Standard**, or **Thorough**. Optionally enter allowed web domains,
   such as `example.org, docs.example.com`. Subdomains are included; URLs and wildcards
   are not accepted. Redirects must remain within the allowed domains.
4. Ask a concrete question and send. Research shows a short plan, gathers and cross-checks
   evidence, then writes a report. Inspect the research plan and progress while it works.
5. Open **[S1]** citations to inspect the retained passages. Use the conversation's Markdown
   export to save the report and its available evidence.

Research uses a model with tool calling enabled and at least three allowed model passes
(**Settings → Model → Max tool rounds**). Assistant restrictions and tool permissions still
apply. Eligible tools include `web_search`, `web_fetch`, `read_web_source`, `query_public_api`,
`search_documents` and the research notebook. After API evidence is captured,
`export_api_dataset`/`collect_api_dataset` can create [validated data files](API_DATASETS.md),
and `analyze_api_dataset`/`create_api_report` support [tables and HTML reports](DATASET_REPORTS.md).
Requested file creation uses normal file-write permission (Ask by default). Execution and
workspace tools require an explicit [analysis handoff](#analysis-handoff); MCP, memory tools
and child agents remain outside Research.
Approvals still appear when the effective permission for an eligible tool is Ask.

Research uses the **current question and selected sources**, rather than replaying older
conversation messages or injecting cross-conversation memories. Include relevant context
in the question. Assistant/system instructions still apply. Document selections constrain
retrieval on the server; a model cannot expand the selection through its tool arguments.
Document-only research disables web tools, but the selected model and embedding provider
may still be cloud services. It is a source restriction, not a local-only data policy.
Image attachments and arbitrary skills are supported in normal chat, not this first
research workflow.

## Depth and limits

These are the built-in defaults. Administrators can change each preset under
**Settings → Configuration → Research allowances** without editing a file or restarting.
The composer reads the current deployment presets; saved reports retain the limits used
for that attempt. Larger allowances permit more work and may increase model/search costs.

| Preset | Evidence passes (including planning/report) | Search calls (web + documents) | Source reads (fetches + retained passages) | Gathering time | Reported-token threshold |
|---|---:|---:|---:|---:|---:|
| Brief | 5 | 3 | 4 | 2 minutes | 20,000 |
| Standard | 12 | 8 | 16 | 15 minutes | 250,000 |
| Thorough | 24 | 24 | 48 | 30 minutes | 1,000,000 |

A lower effective Model round limit wins. For example, Thorough still allows only 12
passes when **Max tool rounds** resolves to 12; choose at least 24 to use its full planned
allowance. Assistant and explicit conversation overrides also apply. The pass allowance includes planning and a reserved final
synthesis pass. For new turns, an approved analysis handoff can use the remaining effective
**Max tool rounds** to finish files beyond the evidence-pass allowance. At that point new
searches, page reads and API collection stop; only retained-data analysis and approved
workspace/code tools remain. Time and token thresholds still apply and never reset.
For example, Standard with Model rounds set to 50 gathers within its 12-pass allowance,
then approved analysis can finish within 50 total passes, reserving the last for synthesis.
Without a handoff, the original 12-pass behavior remains. Duplicate tool requests are not repeated; failures consume attempts too.
If synthesis is empty, truncated or ends without a completion signal, up to two tool-free
recovery calls may continue it using existing evidence. They can exceed the preset's
planned pass count only if they remain within the effective **Max tool rounds** setting.
They do not increase the per-call output/context limits or repeat searches and page reads.
With output guardrail rules active, automatic continuation is disabled to avoid joining
separately checked text into an unchecked sensitive match; the answer remains incomplete.
Gathering stops admitting reads at its limits, and the next pass writes from the available
evidence. Planning and synthesis advertise no tools, and unexpected calls in those stages
are never executed. Search and page fetches also have their own transport limits.
Exhausted search/read tools are removed from the next advertised tool list, and gathering
instructions report remaining allowances. Reported usage from the current model call is
checked before admitting its requested reads, not only before the next model call.
Source storage still permits at most **64 records per turn and 512 per conversation**.
One long page can consume several records; failures also consume records. Once room for
new records is exhausted, Research stops gathering and writes from retained evidence,
including when a batch requests more reads than can be captured. This conservative check
does not attempt to predict whether another page might reuse an existing record.
Available dataset export, column inspection and [HTML reports](DATASET_REPORTS.md) can
still use retained API pages after reads or source storage are exhausted, within the
remaining time/token/pass ceilings. Create requested files before the synthesis handoff;
these tools do not create extra model passes or extend a hard budget. Inspection defaults
to Auto and reports to Ask. Reports support filtered grouped counts and exact known-value
sums, with tables, bar charts and downloadable data/analysis files. Custom scripts and other chart types use the explicit
[analysis handoff](#analysis-handoff), with ordinary execution permissions. See the [report demo](DATASET_REPORTS.md#manual-verification).

Presets are saved as the `research` database configuration section. The admin form accepts
3–100 evidence passes, 1–100 searches, 1–100 source reads, 30–7,200 gathering seconds, and
1,000–5,000,000 reported tokens. Ordinary users can read numerical presets through
`GET /api/settings/research`; only administrators can replace them through
`PUT /api/admin/config/research` (all three complete presets are required).
These ranges bound configuration; they do not increase source, transport or model limits.

New execution snapshots the effective preset. An in-flight run keeps that snapshot; a queued
durable run resolves it when execution begins. Approval resumes apply the lower of each
saved/current admin limit without resetting elapsed time, attempts or reported usage.
Approvals created before this update retain the older preset ceilings. If a lowered planned
pass allowance is already consumed, no pending gathering tools run and the harness attempts
the final report within the remaining generic Model allowance. Regeneration is a new attempt
using current settings, not a continuation of a saved budget.

Time and reported-token thresholds are checked between operations. An in-flight provider
request can take longer, and final synthesis/recovery add time/tokens. Unknown provider usage cannot
be treated as zero. These thresholds and the displayed model costs **are not a guaranteed
invoice ceiling**. Search API credits are separate from model accounting. Provider retries
and fallback behavior retain the [model-call accounting limits](MODEL_CALLS.md).

The progress panel shows searches, source reads, captured source records, remaining source
capacity, model passes used, effective planned passes, the total Model pass ceiling,
gathering thresholds, elapsed time, and available model usage. It identifies stricter limits
applied on resume. It exposes stages and a short plan, not private model reasoning.
Reports should distinguish evidence, inference, contradictory sources, and unanswered
questions. A valid citation is not proof of semantic claim support; verify important claims.
Search snippets alone are not fetched evidence. Reports with no source records are marked
unverified; unavailable sources remain explicitly unavailable in the source drawer.

Long-page reading supports keyword-focused passages and explicit character-offset pagination.
The agent can revisit a retained web citation with `read_web_source` when earlier output
was trimmed, without a new HTTP request or renewed retention. Both fetches and retained
reads consume the read allowance; repeated retained reads are allowed within that budget.
Research rereads are limited to this attempt's captured sources and current domain scope,
including after approval/resume. New Research attempts do not import earlier evidence by
label. See [focused web reading](WEB_SOURCES.md#focused-reading-and-saved-evidence).

## PDF and JSON evidence

Research also reads public **PDF and JSON sources** through `web_fetch`. PDF citations
retain page numbers, and JSON citations retain the selected path and array index range.
Ask for a specific page or set of records when a response is large; the model can use
`pdf_page`, `json_pointer`, `json_start` and `json_limit`. Complete retained passages can
support notebook findings and final writing. Scanned PDFs require OCR, complex PDF tables
need verification. For NIH RePORTER projects, PubMed publications or ClinicalTrials.gov studies, the separate
[public API query tool](PUBLIC_API.md) supports validated pages and continuation from
retained citations. Search metadata does not establish study findings. PubMed record-detail
reading captures available abstracts and author affiliations with separate citations; see
[article reading and the Fred Hutch demo](PUBLIC_API.md#read-abstracts-and-author-affiliations). ClinicalTrials.gov adds condition/status/sponsor/location searches and cited study sections
for eligibility, interventions, sponsor/site verification and posted results. Overall
recruitment, site recruitment and results availability stay distinct; see the
[study demo](PUBLIC_API.md#manual-verification-clinicaltrialsgov-demo). Other POST APIs and bulk data acquisition remain unsupported. See
[PDF and JSON sources](WEB_SOURCES.md#pdf-and-json-sources) for limits and manual checks.

The three API adapters retry temporary failures up to three attempts per HTTP operation,
honoring server delays within the original 30-second deadline. These retries stay within
one Research read and do not repeat successful operations or make additional model calls.
Stop interrupts backoff; citation inspection and dataset manifests retain attempt history.
See [API recovery and its limits](PUBLIC_API.md#temporary-failures-and-automatic-retries).

For large data tasks, use `collect_api_dataset` with a preview `source` label. Bulk
collection stores larger pages privately, charges one Research read per bounded invocation,
and returns a dataset ID rather than a growing list of citations. Explicit `dataset_id`
continuation reuses saved pages; export, inspection and reporting accept the same ID.
Legacy `labels` collection retains its per-page source/read costs. See
[multi-page collection](API_DATASETS.md#multi-page-collection) for defaults and remaining bounds.

## Analysis handoff

The model can call **`begin_research_analysis`** when your request needs custom analysis,
plots, trend lines, annotations or other files beyond the built-in report. The handoff
states its purpose and requested relative output paths, and defaults to **Ask**. Once
approved, eligible `execute_python`, `execute_node`, `run_shell` and workspace file tools
become available. Their own permissions still apply: approving the handoff does not approve
later code execution, and disabled tools remain disabled. Agent mode can auto-approve Ask
tools as usual. MCP, child agents and memory tools are not enabled by this handoff.

Code uses the configured sandbox. Container `network: none` disables networking;
`network: bridge` permits configured outbound access, subject to the actual environment.
The local runner uses host networking; AgentCore depends on its configuration. These are
configuration facts, not proof that a particular endpoint is reachable. **Research domain
filters constrain the reviewed web/API tools, not arbitrary code networking.** Review the
handoff and code accordingly, or retain disabled network execution. Prefer scripts that
analyze the already exported dataset instead of downloading it again.

Each model call receives current execution-tool availability and sandbox configuration
facts. Exact advertised tool names are retained in protected call diagnostics so a claim
that a tool was unavailable can be checked. The shell tool is named `run_shell`, not
`execute_bash`. Provider-side tool filtering still requires provider diagnostics.

Before synthesis the harness checks whether declared output paths contain nonempty files.
Missing outputs and available files are listed separately in the answer and Research
progress. An unfinished delivery is marked incomplete (limit reached when a budget stopped
the work); existing reports and exports are retained. A report under another name is
listed as available, but is not silently treated as the requested chart. This checks file
presence only: it does not prove a chart is scientifically correct, establish event
causality, or claim a browser preview occurred. The model must validate its calculations
and report limitations. Analysis uses the existing overall Model ceiling, never unlimited
retries. Paused turns created before this change retain their original pass policy.

The handoff lists this turn’s existing generated file paths. Use them directly instead of
searching or exporting again. Built-in reports return compact exact grouped results (up to
ten groups per section, with explicit omission counts); full results remain in analysis.json.
Declare an existing report’s actual path when reusing it, and add paths for new charts.
File inventory is bounded to the latest 64 tool-published paths in this attempt.

### Manually checking analysis completion

1. Stay in the **same chat** that collected the data. Select **Research → Standard** and
   send a **new prompt**; you do not need a new conversation. Set **Settings → Model →
   Max tool rounds** to 50 (unless an assistant or conversation override sets a lower ceiling).
2. To reuse a previous collection in the same conversation, copy its `dataset_id` from
   the collection tool result. Ask: “Use retained dataset ID [paste ID] without downloading
   it again. Verify coverage and scope, create an HTML report with annual funding bars and
   a linear trend line, and use begin_research_analysis and execute_python as needed.
   Reuse returned file paths, verify all outputs, and cite the retained data.”
3. Approve the analysis handoff and execution when prompted. Open the report and verify
   the chart and trend line are present. Compare values against the exported analysis.
4. Inspect **Context record → Model calls** for execution-tool availability and the
   effective round ceiling. A longer analysis can pass the preset’s 12 evidence passes
   while staying within 50 total Model passes and the shared time/token thresholds.
5. If work ends early, expand **File delivery** in Research progress. Available reports
   and exports should remain listed separately from missing charts, including after reload.

The first test still depends on the selected model following the tool workflow. Automated
regressions use scripted providers to force a late handoff and verify the budget boundary;
they do not measure a live model’s plotting quality.

### Finishing a report from an earlier collection

A failed or incomplete report does not necessarily mean data collection failed. Check the
collection result for `dataset_id`, `captured_records`, `api_reported_matches` and `complete`.
If collection is complete, ask for analysis of that retained dataset instead of another
download. Research does not automatically replay the earlier conversation, so include the
dataset ID, relevant file paths and the requirements again in your new prompt. Access,
source retention and scope checks still apply to the retained dataset.

Adapt this example, replacing the bracketed placeholders with values from your tool results:

```text
Finish the report using retained dataset_id [paste dataset ID] in this conversation.
Do not download or collect the records again. Verify coverage, organization and agency
scope, duplicate IDs and missing award amounts using the retained data.

Create a self-contained HTML report for [organization and fiscal years] with annual
funding bars in chronological order, a linear trend line, a supporting table of amounts
and project counts, scope limitations and citations to the retained data.

Use begin_research_analysis and execute_python as needed. Existing files are:
[paste exact records.csv and analysis.json paths, if available]
Reuse those files rather than exporting or searching for them again.

Save the report as funding_report.html and embed the chart directly in that HTML so
it displays without separate files. Verify the report and chart before claiming completion.
Do not add event annotations or conduct additional web research for this task.
```

Approve the handoff and code execution when prompted. For a **partial** dataset, explicitly
request continued collection with its `dataset_id` before creating whole-query totals.
Keep any report based on incomplete data clearly labelled as partial.

## Research notebook and working context

During gathering, the model can maintain a **Research notebook** containing concise
findings, source-linked disagreements, and open questions. Expand it in the research
progress panel to inspect the latest revision, including on a saved answer or after a
reconnectable run replays. These are factual working notes, not private model reasoning.
The notebook is specific to this attempt: a new Research request starts fresh.

The `update_research_notebook` tool replaces the complete notebook. Each update must
preserve still-relevant findings and reference accessible **[S#]** passages from this
attempt. It accepts up to 12 findings, four disagreements, and eight open questions;
each finding/disagreement has at most 500 characters and four source labels, and each
question at most 200 characters. Notes cannot cite failed fetches, search snippets, or
invented labels. Checking a source reference does **not** verify that the note is correct
or that its passage supports the claim. Inspect important citations in the report.

After an accepted update, Phlox can omit older, completed tool exchanges from subsequent
model input. It retains the latest two reading results while gathering and keeps exchanges
whose results were not available when the model wrote the notes, including sibling reads
in the same batch. Before synthesis, covered exchanges can all be replaced by
the notebook and the complete retained passages cited by its findings and disagreements.
The original user request and full saved tool transcript remain intact; this changes only
the copy sent to the provider. This avoids repeatedly resending bulky results while giving
the report writer original evidence rather than relying on summaries alone.

Restoration rechecks ownership, current document/domain scope, deletion, expiry, and the
attempt's source usage. It performs no network requests and does not extend retention.
Only whole passages that fit alongside the output reservation are added. The panel reports
condensed exchanges, restored labels, and passages omitted for context limits. An omission
is also sent to the model with instructions to acknowledge the gap. The usual guardrails,
context fitting, model accounting and administrator limits still apply. **Context record →
Model calls** remains the record of actual fitted input and provider usage; notebook
preparation is not a separate model call.

If a notebook source becomes unavailable, its dependent notes and unlinked questions are
withheld from the next model input. Phlox also conservatively withdraws earlier complete
tool exchanges and assistant paraphrases for that attempt, rebuilding from accessible
notebook evidence. Saved messages, tool arguments, earlier progress events, and backups
are not retroactively erased by this check; removing a snapshot is not conversation erasure.

Updates use ordinary tool permissions and model pass/token allowances, without consuming a
search or source-read attempt. They add no hidden summarization call. The tool is advertised
only during Research gathering and can be disabled in the tool manager. A model that does
not update the notebook keeps the existing context-fitting behavior; savings and note
quality depend on the model. This does not increase hard limits or guarantee completion.
Approval snapshots preserve notebook state, and resumes reauthorize evidence before reuse.

To try it, choose **Standard** or **Thorough** Research and ask for a comparison across
several substantial sources, with this extra instruction: “Keep the research notebook
updated with findings, disagreements, and open questions as you read. Cite original
passages in the final report.” Expand **Research notebook** to follow its revisions. On a
long enough investigation, the final panel should report condensed exchanges and restored
passages. Open the final citations to verify their supporting text. With reconnectable runs
enabled, refreshing should recover the same progress without repeating completed searches.

## Stop, reload, and recovery

**Stop** requests cancellation and prevents subsequent tool/model calls, including synthesis.
A provider or DDG request already in flight may return before cancellation completes. A
stopped or failed run retains collected source references and explains that the report was
not completed. Unfinished text may recover within the active turn as described above;
uncertain tool actions and interrupted server runs are never automatically replayed.

Saved answers show an incomplete outcome when recovery cannot finish, or an automatic
continuation notice after successful recovery. **Context record → Model calls** shows the
actual per-call limits, trimming and finish reasons. Increasing Settings now applies to the
next turn in existing chats too, subject to assistant/explicit conversation overrides.
Continue in normal Chat to reuse saved conversation work; a new Research turn still starts
from its current question and selected sources. Per-call output/context limits and provider
capacity are unchanged by a larger preset. More efficient retention of working context,
stage-specific output allowances and live-model quality calibration remain planned.

With [reconnectable runs](RUNS.md) enabled, research events and approval state survive chat
switches and refreshes. Without runs, keep the chat open: its existing request-bound
cancellation behavior still applies. Research does not silently change `runs.enabled`.
Restart recovery follows the normal runs policy; it does not automatically resume research.

Text drafts are recovered per conversation within the current browser tab, including after
refresh. They are kept in session storage, cleared on logout, and never sent before submission.
Attachments and Research selections are not restored with an unsent text draft. Closing the
browser session or clearing its storage may remove drafts. While streaming, scrolling up
pauses automatic following; **Jump to latest** restores it.

## Relationship to the deep-research skill

The built-in `deep-research` skill remains reusable instructions for ordinary chat. Selecting
or automatically loading that skill **does not switch modes**, grant tools, or enable durable
execution. In Research mode, explicitly selected `deep-research` guidance may accompany web
research, but the server's scope, stages, stable citation labels, and budgets take precedence.
Other skills are not loaded in Research mode. Fresh installations receive clarified built-in
instructions; existing skill records are preserved, including user edits.

## Verify budget configuration

1. Open **Settings → Configuration → Research allowances** as an administrator and note
   the current values. Temporarily set Brief's source reads to 1 and save.
2. Select Research → Brief in the composer. Confirm it shows the saved value. Ask for a
   comparison across several specified public pages; at most one page fetch should execute,
   and the report should identify any evidence gaps. Source checks still apply to the page.
3. Inspect the saved progress and **Context record → Model calls**. Preset allowances and
   per-call output/context limits are separate. Stop a second attempt during gathering and
   confirm no report or additional tool calls start afterward.
4. Restore the prior admin values. Select Thorough and check the composer warning if your
   Model round setting is below 24. Raise that setting only if you want the larger allowance;
   assistant and conversation overrides may still affect the resolved limit.
5. With reconnectable runs enabled, reload during gathering and confirm the same counters
   and snapshotted limits return. Saving larger presets must not enlarge an already active
   run or approval; stricter settings take effect when an approval resumes.

## Search engine administration

Go to **Settings → Configuration → Web search** as an administrator. Changes are saved to
the database and apply to new searches immediately. No configuration-file edit is required.

- **DuckDuckGo** is the default, requires no key, and is explicitly selected through the
  `ddgs` library. This is an unofficial search integration, not an unlimited guaranteed API.
- **Serper** uses Google results through Serper's search endpoint. Enter your Serper API key;
  saving masks it, leaving it blank preserves it, and the removal checkbox deletes it after
  switching engines. Search calls consume your Serper credits.
- **SearXNG** accepts an HTTPS public instance URL. Choose an instance from
  [searx.space](https://searx.space/) that permits JSON search. Running your own instance is
  optional. Many public instances disable JSON output or block automated requests; the
  [SearXNG API documentation](https://docs.searxng.org/dev/search_api.html) describes these
  restrictions. A listing is not an availability guarantee.

**Test search** uses the current, unsaved form values and a fixed public query. It may consume
one Serper credit. It shows the actual engine and whether fallback occurred, without saving.
Do not treat a successful DuckDuckGo fallback as evidence that the configured engine works.

On configured-engine errors (including rate limits, denied JSON, malformed responses, or
connection failures), Phlox tries DuckDuckGo once. Valid empty result lists are not errors.
A failed configured service cools down for 60 seconds; searches during that window use
DuckDuckGo without extending the timer. The next search after expiry retries the configured
service. Searches are serialized within this
process and paced (default two seconds, admin range one to thirty seconds), with a bounded
queue wait and no retry storm. If both engines fail, the model receives an explicit failure
and Research can still report gaps using evidence already gathered.

Serper and SearXNG allow up to 20 seconds of socket inactivity while waiting for results,
within a 30-second total request deadline. Stop interrupts the request. Provider failures
are logged under `phlox.search` with the engine, primary/fallback role, exception type, and
HTTP status when available. Queries, API keys, response bodies, and raw exception messages
are omitted. When launched with `scripts/start.sh`, inspect `.run/logs/backend.log`;
`Search cooldown` entries explain when the configured engine is temporarily bypassed.

Queries go to the selected engine and, on failure, DuckDuckGo. Domain filters restrict
returned source URLs and fetches; they do not keep search query text away from the search
provider. No query/results cache is shared between users. Serper keys are write-only through
the API but stored in the DB overlay; protect database backups as described in
[Backup and restore](BACKUP_RESTORE.md). Public search endpoints use bounded, DNS-pinned
public-network connections; private fetch allowlists do not permit private search endpoints.

Legacy `web_search.searxng_url` / `SEARXNG_URL` settings remain seeds until an admin first
saves Web search. Thereafter the saved admin choice takes precedence, including an explicit
return to DuckDuckGo. New deployments should use the admin console.

## Manual verification

1. Start Phlox normally. Confirm Chat is selected and a normal message still works.
2. In Configuration, select Serper, enter your key, test, then save. Confirm the test reports
   `serper` without fallback and the saved form does not reveal the key. Repeat with a public
   JSON-enabled SearXNG instance if desired.
3. Test an invalid Serper key without saving it. Confirm the test reports DuckDuckGo fallback
   (or an explicit failure if DDG is also unavailable), and your saved configuration is intact.
4. Select Research → Selected documents. Choose two documents with a known disagreement and
   ask for a comparison. Verify source labels open their actual passages and the report notes
   the disagreement. The tool log must contain no web/shell/MCP calls.
5. Try Web research with a domain filter. Check fetched URLs and citations against the filter.
   Compare the report with normal Web search on the same question and model: usefulness,
   supported claims, disagreements, latency, and model/search cost matter more than length.
6. Stop during gathering. Confirm no later synthesis or tools start. With runs enabled, also
   refresh mid-research and verify the same plan/counters return without duplicate actions.
7. Type different unsent drafts in two chats, switch between them, then refresh. While a reply
   streams, scroll upward; it should stay where you are until you choose Jump to latest.

No live-model quality or provider availability guarantee follows from scripted regression
checks. Keep provider/model versions with any research quality comparison.
