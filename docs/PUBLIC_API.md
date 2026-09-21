# Public API queries

[User Guide](USER_GUIDE.md) · [Research](RESEARCH.md) · [Captured sources](WEB_SOURCES.md)

`query_public_api` reads **NIH RePORTER projects** and **PubMed publication metadata**.
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

Captured fields are PMID, title, author names, journal, publication date, volume, issue,
pages, DOI/PMC identifiers when returned, and the PubMed record link. **Abstracts and full
article text are not retrieved in this slice.** These records support bibliographic claims;
they do not establish study findings, quality or clinical recommendations. Abstract retrieval
and ClinicalTrials.gov integration remain planned.

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

For either adapter, continue with **only `continue_from: "S1"`**, substituting the previous
page's citation label. An optional adapter must match that source. Phlox reuses the retained
query, sort and page size; the model cannot change them or supply arbitrary offsets,
endpoints, headers, credentials or POST bodies. Continuation rechecks retained content and
pagination provenance, ownership, expiry and the current Research attempt/domain scope.
Deleted/expired citations cannot authorize requests. A new Research attempt needs a fresh query.

Both adapters accept 1–20 records per page. The complete selected fields must fit one
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
Export one query at a time. Partial data remains labelled partial; no bulk download or
unrequested calculation is performed by the query tool.

## Policy and operations

The registered read tool defaults to automatic permission; Tool Manager can require
approval or disable it. Dataset file creation separately defaults to **Ask**. Research
stays opt-in. Domain restrictions must allow `api.reporter.nih.gov` for NIH, or
`eutils.ncbi.nlm.nih.gov` for PubMed. Allowing only `pubmed.ncbi.nlm.nih.gov` is insufficient:
that is the public website, not the API host.

Every attempted page consumes one Research read, including failed pages. A PubMed page's
two requests share that read and the same 30-second deadline. Source capacity is checked
before dispatch. Counters and current-policy checks survive approvals and durable replay.

Both adapters use DNS-pinned connections, the private-network policy, no cookies/credentials
or environment proxies, and a 2 MiB limit per response. The deadline covers pacing,
network reads and isolated parsing; Stop interrupts these operations. NIH requests start
at most once per second; PubMed starts are spaced by at least 0.4 seconds within Phlox's
single process. Other applications sharing the same public IP may also consume NCBI's
rate allowance. There is no API-key configuration or automatic retry in this slice.
Compression, redirects and non-JSON responses are rejected; failures reveal no response bodies
and create no usable evidence/cursor.

These are reviewed adapters, not arbitrary public API access. New adapters must define
endpoints, request policy, validation, pagination and export fields. Generic `web_fetch`
remains GET-only. ClinicalTrials.gov, abstract retrieval, bulk acquisition, configurable
retry/backoff and general API discovery remain backlog work.

References: [NIH RePORTER API](https://api.reporter.nih.gov/),
[NCBI E-utilities parameters and usage guidance](https://www.nlm.nih.gov/dataguide/eutilities/utilities.html),
[PubMed ESearch window](https://www.nlm.nih.gov/pubs/techbull/so22/so22_updated_pubmed_e_utilities.html).
