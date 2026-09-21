# Public API queries

[User Guide](USER_GUIDE.md) · [Research](RESEARCH.md) · [Captured sources](WEB_SOURCES.md)

`query_public_api` reads **NIH RePORTER projects**, **PubMed publications**, and **ClinicalTrials.gov studies**.
It works in ordinary Chat with **Web search enabled** and in opt-in Research with Web
or mixed sources, subject to assistant tool access and Tool Manager policy. No API key,
configuration change, database migration or separate service is required.

## Try PubMed

Select Research with Web sources (or enable Web search in ordinary Chat), then ask:

> Use query_public_api with adapter pubmed to search asthma[Title] AND 2024[pdat].
> Retrieve exactly two pages of two records each, using continue_from for the second
> page. List the four PMIDs, titles, journals and publication dates with citations.
> State the API-reported match count and whether more pages remain. Then export those
> two pages as downloadable data files. Do not infer study findings from the titles.

The initial arguments are `adapter: "pubmed"`, `query`, and optionally `limit` (default 2).
The query supports PubMed field tags, Boolean expressions and date filters. Phlox retains
PubMed's **query translation** so you can inspect how the service interpreted the query.
Results are sorted by publication date, most recent first; PMIDs need not be numerically
ordered. Dates retain the API's original text, including month/season-only dates.

Each nonempty page uses two fixed GET operations: **ESearch** identifies the page's PMIDs,
then **ESummary** retrieves their bibliographic metadata. A zero-result search needs only
ESearch. Missing/mismatched summaries, ignored-field/term warnings, duplicate IDs, changed
query translations or changed match counts fail explicitly before evidence is captured.
The reported translation is inspectable; this does not independently prove a complex
search expresses your intended inclusion criteria.

Search-page fields are PMID, title, author names, journal, publication date, volume, issue,
pages, DOI/PMC identifiers when returned, and the PubMed record link. These search records
support bibliographic claims, not study findings. Read selected abstracts and author
affiliations separately as described below. Full article text remains planned; ClinicalTrials.gov study reading is described below.

## Read abstracts and author affiliations

After a PubMed search, `query_public_api` can inspect a selected article with
`record_from: "S1"` (the query-page citation) and `record_id: "12345678"` (a PMID on that
page). The adapter follows the retained source; an optional `adapter` must match it.
This is a fixed EFetch XML request, not an arbitrary URL. It creates a **new detail citation**;
the original search metadata stays unchanged.

- `section: "abstract"` is the default. Abstract text preserves supplied headings and
  language labels, and reports `abstract_status: "missing"` when none is available in the
  returned record. Inline formatting becomes text. Use `start` and `max_chars` (500–4,000,
  default 4,000) for long abstracts. `next_start` identifies unread text. Partial passages
  remain explicit; a missing abstract is not a failed network request.
- `section: "authors"` returns names with their own supplied affiliations, author positions,
  collective-author names, reported list completeness and missing-affiliation counts. Use
  `affiliation: "Fred Hutch"` for a case-insensitive substring filter, or omit it to inspect
  all returned authors. `start` indexes the matching list; `limit` is 1–20, default 5.
  Legacy affiliations lacking author mappings are reported separately and never assigned
  to every author. No matches with missing affiliations cannot establish institutional absence.

Follow the returned `next_start` with the same section/filter and the **detail citation** as
`record_from`. This verifies that the normalized article record still matches the earlier
capture. It also works when switching from abstract to authors. If the record changed,
start explicitly from the search citation again and keep versions separate. Source deletion,
expiry and Research attempt/domain scope are checked before retrieval and again before capture.

Every detail call refetches one selected article under shared PubMed pacing and consumes
one Research read. To revisit already captured evidence without network access, use
`read_web_source` with its citation. Detail snapshots retain their selection, source lineage,
request hash and record version through rereads, notebooks, exports and reloads. They do
not authorize `continue_from`, which is reserved for search-page pagination.

Each serialized detail selection must fit 6,000 characters. Oversized selections fail
explicitly; reduce `max_chars` or the author `limit`. Pathological single-author affiliation
lists may still exceed this bound. XML is parsed in the bounded worker without resolving
DTDs or entities; malformed, mismatched, multi-record and unsupported book responses are
rejected. Public journal articles are supported; full-text acquisition is not included.

Affiliations describe the publication record, not current employment. Abstracts support
summaries of what the authors report; they do not establish independent verification or
replace reading the full methods/results. Missing fields remain unknown.

### Manual verification: Fred Hutch demo

Use **Research → Web → Standard** (or normal Chat with **Web search enabled**) and submit
this as one request:

> Find four recent PubMed publications about ovarian cancer involving Fred Hutchinson
> Cancer Center or its earlier name, Fred Hutchinson Cancer Research Center. Retrieve two
> search pages with two records each. Read the available abstracts and author affiliations
> for those four PMIDs using query_public_api record_from/record_id. Summarize each study's
> objective and reported findings with detail citations. Identify Fred Hutch-affiliated
> authors only where their returned affiliations support it. Note missing abstracts or
> affiliations and any passages left unread. Export the search records and captured details.

Check that search results get their own citations, and abstract/author reads create separate
ones. Open a detail citation: it should show the PMID, section, selection range and whether
more remains. A Fred Hutch affiliation filter should return mapped authors rather than
labelling every coauthor. Export should produce the usual four files plus
`record_details.json` when `detail_labels` are supplied. Inspect its filters/ranges and the
manifest's `record_detail_sources`. Reload and verify saved downloads and citations still
work. With Agent mode off, file creation asks for permission; reading uses normal query-tool
policy. The exact live publications, abstracts and match counts can change.

## Try ClinicalTrials.gov

Use `adapter: "clinical_trials"` with `condition: "ovarian cancer"`. Optional filters are
`statuses: ["RECRUITING"]`, `sponsor` (sponsor/collaborator expression), `location` (location
expression), and `query` (other terms, such as `"Fred Hutch"`). These use the service's
search semantics rather than exact institution matching. ID-only expressions are rejected
because the API can ignore filters for those queries. `limit` defaults to 2 (maximum 20).

Search pages retain NCT IDs, titles, overall recruitment status, posted-results availability,
lead sponsor, phases, last-update-posted dates, and study links. Missing values stay null.
Studies are ordered by last-update-posted date, most recent first. `continue_from: "S1"`
reuses the saved filters and opaque API page token; users/models do not supply tokens.
The API reports the match count on the first page only; later pages retain that original
count and cannot independently detect every upstream change. Duplicate adjacent records, repeated
page tokens, malformed records and mismatched recruitment statuses fail before capture.
Pagination is not a frozen snapshot; a contradictory total or excess records require a fresh search. Empty pages
can still carry a next token. If the service ends before all reported matches are captured,
the tool and export explicitly report partial coverage.

To read a study returned on a query page, supply `record_from`, its `record_id` (the
`NCT########` identifier), and one of these sections:

| Section | Returned evidence |
|---|---|
| `overview` (default) | Identifiers, sponsors/collaborators, status and registry dates, conditions, design/phases, and description |
| `eligibility` | Eligibility criteria, age, sex and other supplied eligibility fields |
| `interventions` | Arms, intervention names, types, descriptions and mappings |
| `locations` | Returned facilities, site recruitment statuses and contacts |
| `results` | Posted results modules when returned; explicitly missing otherwise |

Each section is rendered as JSON text and read in character passages: `start` defaults to
0 and `max_chars` to 3,000 (500–4,000 allowed). The response retains section status, the
selection range, `next_start`, study metadata and a hash of the full returned study record.
Follow `next_start` using the latest detail citation as `record_from`, with the same section,
to detect record changes between passages. Switching sections can use the same detail citation.
Partial JSON text can omit groups, denominators, units or qualifying text; read the complete
relevant context before summarizing results. Missing results are not evidence of failure.
The shared 2 MiB response, 500,000-character section and 6,000-character serialized evidence
bounds apply. Oversized selections fail explicitly; reduce `max_chars` where applicable.

Each detail call refetches one study from the fixed public v2 endpoint and counts as one
Research read. Retained citations can be reread without network access. Downloads, Stop,
ownership, source expiry, Research scope and optional detail exports follow the same rules
as PubMed. No API key or new configuration is needed. There are no automatic retries or
bulk downloads in this slice.

**Interpretation:** overall `RECRUITING` does not mean every site is recruiting.
`has_results` describes posted results independently of recruitment. Dates are registry
values, not fetch timestamps. Keyword matches do not prove Fred Hutch sponsorship or a
Fred Hutch site: inspect the returned sponsor/site fields and cite that specific evidence.
Registry records are supplied reports, not independent verification or patient eligibility
assessments. Missing fields remain unknown.

### Manual verification: ClinicalTrials.gov demo

Choose **Research → Web → Standard** or normal Chat with **Web search enabled**:

> Use query_public_api with adapter clinical_trials to find recruiting ovarian cancer
> studies matching Fred Hutch. Retrieve exactly two pages with two records per page,
> using continue_from for the second page if available. State the reported match count
> and whether more pages remain. For the first two studies, read overview, eligibility,
> interventions and locations; continue any needed passages. Identify the Fred Hutch
> connection only where sponsor or site fields support it. Distinguish overall recruitment
> from site status and posted-results availability. Cite the detail evidence and export
> the search pages plus captured study sections. Report missing or unread information.

Inspect a detail citation: it should show the NCT ID, section and passage range. Check
sponsors/site statuses against the captured text, not just the search phrase. The export
should contain the four base files plus `record_details.json`; its manifest keeps query
coverage separate from detail selection coverage. Reload and verify saved downloads and
citations still work. An optional follow-up can inspect a `results` section; the response
must distinguish missing results from available evidence. Live counts and records change.

## Try NIH RePORTER

> Use the NIH RePORTER public API tool to inspect projects matching Johns Hopkins for
> fiscal year 2024. Request two records per page and read only the first two pages.
> Show the project IDs, actual organization names, years and award amounts with citations.
> State how many matches the API reports and whether more pages remain. Do not calculate
> annual funding totals from this sample.

The agent supplies `org_names`, `fiscal_years`, and optionally `limit` (default 5).
`adapter: "nih_projects"` is optional for compatibility with existing queries. This adapter
uses one documented POST search per page. It accepts 1–5 organization fragments and
1–10 fiscal years (1985–2100); empty filters, wildcards and unsupported fields are rejected.

Returned name/year filters, parent-project status, types, counts and offsets are validated.
Application IDs must be unique and ascending, including across adjacent pages. Captures
preserve numeric spellings, null amounts, organization identifiers and agency fields.
Name fragments can match multiple legal entities; RePORTER includes non-NIH agencies.
**A sample or known-amount sum is not verified annual NIH funding.** Agency scope, entity
matching, fiscal-year completeness and monetary definitions still require analysis.

## Pagination, citations and exports

For any adapter, continue with **only `continue_from: "S1"`**, substituting the previous
page's citation label. An optional adapter must match that source. Phlox reuses the retained
query, sort and page size; the model cannot change them or supply arbitrary offsets,
endpoints, headers, credentials or POST bodies. Continuation rechecks retained content and
pagination provenance, ownership, expiry and the current Research attempt/domain scope.
Deleted/expired citations cannot authorize requests. A new Research attempt needs a fresh query.

All adapters accept 1–20 records per page. The complete selected fields must fit one
6,000-character passage; oversized pages fail and require a smaller limit. Records are
never silently dropped to fit. PubMed exposes at most its first 10,000 matching records;
NIH continuation uses its supported offset window of 14,999. Window exhaustion is explicitly
incomplete: narrow the query. Server-side limits/errors may stop retrieval sooner.
Neither offset pagination nor stable counts prove that the upstream database stayed frozen.

Click a citation for the fields, record range, request recipe and (for PubMed) interpreted
query. Opening the bare API endpoint does not replay the saved operation. PubMed's recipe
records its search parameters; the retained PMIDs identify the ESummary request. Source
inspection, rereads and Markdown exports do not contact the API again.

When files are requested, [dataset export](API_DATASETS.md) creates records CSV/JSON,
an adapter-specific summary and a provenance manifest from available source labels.
Optional `detail_labels` add the captured article/study selections in `record_details.json`.
Export one query at a time. Partial data remains labelled partial; no bulk download or
unrequested calculation is performed by the query tool.

## Policy and operations

The registered read tool defaults to automatic permission; Tool Manager can require
approval or disable it. Dataset file creation separately defaults to **Ask**. Research
stays opt-in. Domain restrictions must allow `api.reporter.nih.gov` for NIH, or
`eutils.ncbi.nlm.nih.gov` for PubMed. Allowing only `pubmed.ncbi.nlm.nih.gov` is insufficient:
that is the public website, not the API host. ClinicalTrials.gov requires
`clinicaltrials.gov` in a restricted-domain Research request.

Every attempted page consumes one Research read, including failed pages. A PubMed page's
two requests share that read and the same 30-second deadline. Each selected article/study
detail request is another read with its own shared transport/parser deadline. Source capacity is checked
before dispatch. Counters and current-policy checks survive approvals and durable replay.

All adapters use DNS-pinned connections, the private-network policy, no cookies/credentials
or environment proxies, and a 2 MiB limit per response. The deadline covers pacing,
network reads and isolated parsing; Stop interrupts these operations. NIH requests start
at most once per second; PubMed starts are spaced by at least 0.4 seconds within Phlox's
single process. ClinicalTrials.gov requests are spaced by at least 0.5 seconds. Other applications sharing the same public IP may also consume NCBI's
rate allowance. There is no API-key configuration or automatic retry in this slice.
Compression and redirects are rejected; search responses must be JSON;
detail responses are PubMed XML or ClinicalTrials.gov JSON; failures reveal no response bodies
and create no usable evidence/cursor.

These are reviewed adapters, not arbitrary public API access. New adapters must define
endpoints, request policy, validation, pagination and export fields. Generic `web_fetch`
remains GET-only. Full article text, bulk acquisition, configurable
retry/backoff and general API discovery remain backlog work.

References: [NIH RePORTER API](https://api.reporter.nih.gov/),
[NCBI E-utilities parameters and usage guidance](https://www.nlm.nih.gov/dataguide/eutilities/utilities.html),
[PubMed ESearch window](https://www.nlm.nih.gov/pubs/techbull/so22/so22_updated_pubmed_e_utilities.html),
[NLM structured abstract definition](https://dtd.nlm.nih.gov/ncbi/pubmed/doc/out/250101/el-AbstractText.html),
[NLM EFetch XML examples for author affiliations](https://www.nlm.nih.gov/dataguide/classes/edirect-for-pubmed/samplecode2.html).

ClinicalTrials.gov references: [official v2 OpenAPI specification](https://clinicaltrials.gov/api/oas/v2), [API reference](https://clinicaltrials.gov/data-api/api), [study data structure](https://clinicaltrials.gov/data-api/about-api/study-data-structure).
