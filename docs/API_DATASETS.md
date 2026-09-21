# Exporting retained API datasets

[Public API queries](PUBLIC_API.md) · [Research](RESEARCH.md) · [User Guide](USER_GUIDE.md)

When a task asks for downloadable data, `export_api_dataset` can turn retained NIH
RePORTER, PubMed or ClinicalTrials.gov query pages into four base files in a new conversation workspace folder:

| File | Contents |
|---|---|
| `records.csv` | NIH project fields; PubMed bibliography; or ClinicalTrials.gov study IDs, titles, recruitment status, results availability, sponsors, phases and dates |
| `records.json` | All fields retained by the API adapter, including nested data and original numeric spellings |
| `summary.csv` | Dataset coverage, NIH counts/known award sums/missing amounts grouped by organization and fiscal year; PubMed/ClinicalTrials.gov captured and reported record counts |
| `record_details.json` (optional) | Captured article/study section selections, with record IDs, versions, filters and ranges; only when `detail_labels` are supplied |
| `manifest.json` | Query recipe, source references/capture times, HTTP attempt history when captured, coverage and gaps, duplicate counts, and data-file hashes |

The tool reads existing source snapshots. It makes no network requests, installs nothing,
and runs no model-generated code. For multiple pages, `collect_api_dataset` combines
validated acquisition and export as described below. Neither tool enables arbitrary
shell execution. For tables and bar charts, use the separate
[dataset report workflow](DATASET_REPORTS.md) with the retained source labels.

## Multi-page collection

For large tasks, inspect a small `query_public_api` preview, then call
`collect_api_dataset` with **`source: "S1"`** (the actual preview label). Use one query
covering all requested fiscal years/filters. The preview must start at offset zero.
The server collects larger validated pages into a private SQL dataset and returns a
`dataset_id`, compact coverage, one dataset-manifest citation and exported file paths.
Raw pages never fill the model's context or consume one citation each.

NIH bulk requests use up to **500 projects per page**; PubMed and ClinicalTrials.gov use
up to **100 records per page**. The original preview is reused. API filters, ordering,
record identity, totals, request/content hashes and opaque continuation cursors remain
validated. NIH excludes subprojects, so parent/subproject and agency scope must still be
reviewed before describing a sum as institutional or NIH-only funding.

| Bulk argument | Default | Maximum / meaning |
|---|---|---|
| `source` | Start selector | One offset-zero preview citation; repeating it reuses its dataset |
| `dataset_id` | Resume selector | Returned private dataset ID; use instead of `source` |
| `max_pages` | 100 | 200 additional page attempts per call |
| `max_records` | 10,000 | 50,000 retained records in total; increase explicitly to continue beyond it |
| `max_seconds` | 300 | 600 acquisition seconds; remaining Research time can shorten this |

Each bulk invocation charges **one Research read**, regardless of internal page count.
Normal query/export permissions, API rate pacing/retries, SSRF protection, Stop, per-page
network/parser bounds and Research time/token/pass limits still apply. This is not an
unlimited background job. Upstream search windows (NIH offsets through 14,999; PubMed's
10,000-result window) can still require narrower queries. A bulk parsed page is bounded
to 1,048,576 characters; the existing HTTP response-byte and parser limits still apply.

Each validated page is committed before the next request. Stop publishes no new files;
partial acquisition can export explicitly partial data. Resume with `dataset_id` to reuse
saved pages, including after interruption. An explicit dataset reference can be reused in
a new Research attempt subject to current ownership/domain/expiry checks; old conversation
messages and unrelated evidence are still not injected. Process loss never automatically
replays an uncertain tool action. Progress reports the checkpoint ID and record count.

Storage is bounded to **16 MiB per retained dataset**, **32 datasets / 64 MiB per
conversation**, and **32 MiB per bulk export/report bundle**. Data is private to the owning
conversation, included in database backups, and expires with its preview source. Removing
the preview or a dataset-manifest citation removes the retained dataset. Deleting the
conversation cascades to its datasets. Existing exported files and saved answer copies
are independent and are not erased by source removal.

`analyze_api_dataset`, `export_api_dataset` and `create_api_report` accept **`dataset_id`**
instead of `labels`. The export manifest includes per-page requests/hashes, coverage and
the dataset checkpoint. Inspection and reporting work from the full retained dataset.
A manifest citation establishes acquisition/coverage; inspect records or generated tables
before making record-level claims. Detail exports still use the small-page `labels` path.

### Manual verification: ten years and a custom chart

Choose **Research → Web → Standard** and a tool-capable model. Ask:

> Find Fred Hutchinson Cancer Center's RePORTER projects for FY2016–2025 in one query.
> Inspect a small preview, then use collect_api_dataset with its source label to collect
> every reported match. Verify organization and agency scope, coverage, duplicate IDs and
> missing amounts. Export the data and create an HTML funding report with annual bars and
> a linear trend line. Use begin_research_analysis and execute_python if custom plotting
> is needed. Explain scope limitations and support any event annotations with evidence.
> Do not present partial data as complete annual totals.

Approve the collection, analysis handoff and execution when prompted (unless already
allowed by policy/Agent mode). Expect hundreds of records per NIH bulk request, a compact
checkpoint, complete coverage before totals, and data/report files. Stop during collection
and continue with its dataset ID to check that saved pages are not downloaded again.
The [analysis handoff](RESEARCH.md#analysis-handoff) also explains sandbox networking.

### Legacy citation-page collection

The older `labels` interface below remains compatible with saved tool calls and small
page workflows. It retains its original limits and charges one read/citation per page.
Prefer `source` / `dataset_id` for large acquisitions.

When downloadable data is requested, first use `query_public_api` to inspect a small
page and verify the query. Then use **`collect_api_dataset`** with its saved `labels`.
The collector supports NIH RePORTER, PubMed and ClinicalTrials.gov through the same
reviewed adapters. It follows the saved query, page size and continuation cursor; it
accepts no new filters, arbitrary URLs, raw request bodies or code.

Each call collects a bounded number of additional pages and creates the four base files
above. The response contains counts, completeness, stop reason, source labels and file
links, keeping the new records out of the model's context. Record-level claims still
require reading the saved citations. PubMed collection contains bibliography, not
abstracts or full articles; ClinicalTrials.gov collection contains search metadata,
not expanded study sections. Existing detail reads/exports remain separate.

| Argument | Default | Maximum / meaning |
|---|---|---|
| `labels` | Required | All retained query pages in order, starting at offset zero; at most 64 labels |
| `max_pages` | 5 | 20 **additional page attempts**, including failed pages |
| `max_records` | 200 | 1,000 records **in total**, including the supplied pages |
| `max_seconds` | 60 | 120 seconds for acquisition; Research's remaining time can shorten it |

The original page size is preserved. Collection stops before requesting a page that
could exceed the record ceiling, so a result can stop below `max_records`. Raising a
record ceiling requires an explicit new choice; continuing with the same ceiling does
not increase it. Existing source storage limits and the **2 MiB bundle limit** still
apply, with space reserved for collection provenance. This is bounded collection, not
an unrestricted bulk-download service.

**Permissions and budgets:** the tool defaults to **Ask** because it creates files.
Normal Chat requires Web search enabled; Research remains opt-in and advertises collection
after an API preview. Disabled/denied query or export tools cannot be bypassed through
collection, and read-only sub-agents cannot use it. Each additional page attempt consumes
one Research read. HTTP retries and PubMed's two requests per page stay within that read.
Every page shares the overall acquisition deadline while retaining the usual per-page
30-second maximum. Publishing already retained records follows acquisition; Stop prevents
new publication. Normal file approval, ownership, source expiry and domain checks apply.

**Progress and continuation:** live tool output shows saved page/record counts and the
complete label list after each page. Validated pages are committed to the source store
individually. On an API failure or allowance limit, the collector exports a clearly marked
partial dataset if the retained prefix is still valid and authorized. `manifest.json`
adds a `collection` section containing stop reason, limits, labels, page attempts, whether
a next page exists, and continuation arguments where applicable. `complete` requires both
coverage of the API-reported count and no remaining continuation cursor; neither proves
upstream data completeness or appropriate institutional/funding scope.

To continue, supply the **entire returned label list**, with suitable explicit limits.
Existing pages are revalidated and reused, not downloaded again. Missing, reordered,
conflicting, removed or expired pages cannot be silently skipped. Source and API window
ceilings may require a narrower new query. If export fails, the retained citations remain
available even though no completed bundle is reported.

**Stop:** already saved pages remain, but a stopped call does not publish a new bundle.
Use the saved tool result/progress labels to continue explicitly. After a stopped Research
attempt, switch to ordinary Chat with Web search enabled to reuse those citations; a new
Research attempt retains its existing fresh-evidence boundary. There is no automatic
replay after process loss. A forced termination can leave saved sources or a published
bundle without a final tool result; existing conservative run recovery still applies.

### Manual verification: collect and continue

In a new conversation, use Research → Web → Standard, or normal Chat with Web search
enabled. Request:

> Use query_public_api with adapter pubmed to search
> ("Fred Hutch"[Affiliation] OR "Fred Hutchinson"[Affiliation]) AND ovarian cancer.
> Inspect one page with limit 2. Then use collect_api_dataset with that page's label,
> max_pages 2 and max_records 6. Stop after this collection; do not fetch article details
> or more pages. Provide the downloadable data files and say whether the six-record
> sample covers all reported matches. Do not infer findings from article titles.

1. Approve collection if prompted. Confirm progress shows saved pages, then four file
   cards appear. With at least six matches, expect three pages and six unique PMIDs.
2. Inspect `manifest.json`: verify coverage, `collection.labels`, page attempts and the
   stop reason. A larger match count must be reported as partial.
3. For a follow-up turn, use **Chat with Web search enabled** to reuse the earlier
   citations. Ask: **Continue that collection using all its saved labels, with max_pages 2 and
   max_records 10. Export the expanded dataset and stop.** Confirm only later pages are
   requested, and a new bundle preserves earlier records without duplicates.
4. For Stop, request a larger bounded collection, stop during progress, and inspect the
   saved labels. Explicitly continue in Chat with Web search enabled. Retained pages
   should be reused. Existing downloaded bundles remain unchanged.

The deterministic local tests cover unavailable/rate-limited APIs, deadline/record/read
limits, Stop, approvals, revocation and all three adapters:
`uv run pytest tests/test_api_collection.py` from `backend`.

## Manual verification

Choose **Research → Web → Standard**, or use normal Chat with **Web search enabled**.
Submit the entire request together:

> Use query_public_api to inspect NIH RePORTER projects matching Johns Hopkins for fiscal
> year 2024. Retrieve exactly two pages of two records each. Then use export_api_dataset
> on those two source labels to create downloadable data files. Report whether this is a
> partial dataset. Do not fetch more pages or describe the sample as annual funding totals.

1. Confirm that two API calls occur and the export does not refetch them.
2. With **Agent mode off**, approve `export_api_dataset` when prompted. Its default
   permission is **Ask**; normal Tool Manager overrides still apply. Selecting Research
   alone does not bypass file-write permission.
3. Confirm all four file cards appear. Download `records.csv` and `records.json` and verify
   the four project IDs and values agree with the citations.
4. Inspect `manifest.json`: it should report four captured unique records, the API's
   larger match count, `all_reported_records_captured: false`, and the missing record range.
   Each source retains its exact request, offsets, original capture time and content hash.
   New API captures also retain `retrieval` operations, attempt statuses and retry delays.
   Export copies this history without repeating network requests.
5. Inspect `summary.csv`. Counts cover the exported records, and sums cover **known amounts
   only**. Missing amounts are counted separately; a group with no known amounts has a
   blank sum, not zero.
6. Refresh the conversation and download the saved answer files again. They should remain
   available through the existing answer snapshot mechanism. Subsequent workspace edits
   do not rewrite saved copies.

For approval rejection, repeat in a fresh conversation with Agent mode off and deny the
export. No dataset files should be created. Research can still report its retained evidence.

For PubMed, use the [two-page example](PUBLIC_API.md#try-pubmed). Verify four PMIDs in
`records.json`, a partial coverage summary with no funding columns, and the interpreted
query in `manifest.json`. Authors and DOI/PMC lists remain arrays in JSON and are joined
with semicolons in CSV. Search-page records do not include abstracts. Use `detail_labels` to add previously captured
abstract/author selections separately. Full article text is not retrieved.
The publication dates remain source strings; no publication-year aggregation is inferred.

For ClinicalTrials.gov, use the [study demo](PUBLIC_API.md#manual-verification-clinicaltrialsgov-demo).
Search records keep overall recruitment status separate from posted-results availability.
Optional details retain sponsor/site/eligibility/intervention/results passages; a captured
section may be partial. The manifest preserves page tokens in per-source requests, while
the common query identity excludes pagination tokens. Registry dates remain source strings.

## Validation and limits

The agent supplies 1–64 distinct query source `labels`, plus optional `detail_labels`, with
at most 64 distinct labels combined. It supplies no file contents or model-authored rows.
Detail IDs must occur in the selected query pages and use the same adapter. Mixed detail
versions of a record are rejected. Detail selections stay separate from query records and
do not change the query coverage statistics; absence from the detail file means unread,
not that an article/study section does not exist. Each selection retains its own provenance.
All pages must be available, owned by the same conversation and from one query of one supported API.
Research additionally requires the current attempt and allowed domains. Normal Chat can
export retained pages from an earlier turn in the same conversation. Access and expiry
are checked again immediately before file publication.

The exporter revalidates source/request hashes, API filters and record types, pagination
metadata and ordering. Mixing different filters, changing reported totals, inconsistent
offsets, or conflicting versions of a record rejects the export. Identical records at
overlapping offsets are deduplicated and counted in the manifest. Missing ranges are
reported explicitly; they do not prevent exporting a clearly labelled partial dataset.

`all_reported_records_captured: true` means the selected pages cover positions zero
through the API-reported match count with consistent unique records. It does **not** prove
that the upstream dataset was frozen, the query covered an entire organization, a fiscal
year is complete, or the records represent NIH-only funding. The adapter excludes
subprojects; entity/agency scope, award definitions and cross-query reconciliation still
need analysis. Never substitute these sums for verified institutional annual funding.

NIH amounts use exact decimal arithmetic, with explicit bounds of 100 significant digits and
exponents between −100 and 100. Nulls remain null in JSON and blank in CSV. Original text
and nested data remain in JSON; formula-like text cells in CSV receive an apostrophe
prefix so opening the file in a spreadsheet does not interpret them as formulas.

The small-page labels bundle is limited to 2 MiB; bulk dataset bundles allow 32 MiB. They are staged privately and published
together under a fresh `api-dataset-…` folder; existing files are never overwritten.
Stop and write failures before publication remove the staging folder. File descriptors
are returned only after publication succeeds. A forced process kill can leave a staging
folder or a published but unreported bundle; such actions are never automatically replayed.

Research advertises the export after an API page has been captured, with at most two
export attempts per turn. It uses no search/read allowance, and can use retained pages
after the read or source-storage allowance is exhausted. It still needs a gathering pass
before synthesis and observes time, reported-token and model-pass ceilings. The model must
call the tool before handing off to final writing; general semantic deliverable tracking
remains future work. Approval state retains attempts and rechecks source access on resume.

Artifacts use existing workspace isolation, checkpoints, downloads and saved answer
snapshots. Removing/expiring a source later does not erase previously exported files,
answers or backups. No schema migration, new configuration or dependency is required.
