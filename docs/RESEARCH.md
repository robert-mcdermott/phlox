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
apply. Only `web_search`, `web_fetch`, and `search_documents` are eligible; Research does
not enable shell/code execution, MCP, file mutation, memory tools, or child agents.
Approvals still appear when the effective permission for an eligible read tool is Ask.

Research uses the **current question and selected sources**, rather than replaying older
conversation messages or injecting cross-conversation memories. Include relevant context
in the question. Assistant/system instructions still apply. Document selections constrain
retrieval on the server; a model cannot expand the selection through its tool arguments.
Document-only research disables web tools, but the selected model and embedding provider
may still be cloud services. It is a source restriction, not a local-only data policy.
Image attachments and arbitrary skills are supported in normal chat, not this first
research workflow.

## Depth and limits

| Preset | Planned model passes | Search calls (web + documents) | Page fetch attempts | Gathering time | Reported-token threshold |
|---|---:|---:|---:|---:|---:|
| Brief | 5 | 3 | 4 | 2 minutes | 20,000 |
| Standard | 8 | 6 | 8 | 5 minutes | 40,000 |
| Thorough | 12 | 10 | 16 | 10 minutes | 80,000 |

A lower user round limit wins. The pass allowance includes planning and a reserved final
synthesis pass. Duplicate tool requests are not repeated; failures consume attempts too.
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

Time and reported-token thresholds are checked between operations. An in-flight provider
request can take longer, and final synthesis/recovery add time/tokens. Unknown provider usage cannot
be treated as zero. These thresholds and the displayed model costs **are not a guaranteed
invoice ceiling**. Search API credits are separate from model accounting. Provider retries
and fallback behavior retain the [model-call accounting limits](MODEL_CALLS.md).

The progress panel shows searches, page reads, captured source records, elapsed time, and
available model usage. It exposes stages and a short plan, not private model reasoning.
Reports should distinguish evidence, inference, contradictory sources, and unanswered
questions. A valid citation is not proof of semantic claim support; verify important claims.
Search snippets alone are not fetched evidence. Reports with no source records are marked
unverified; unavailable sources remain explicitly unavailable in the source drawer.

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
from its current question and selected sources. Preset calibration and configurable larger
investigation budgets remain planned; the preset numbers above have not been increased.

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
