# Public API queries

[User Guide](USER_GUIDE.md) · [Research](RESEARCH.md) · [Captured sources](WEB_SOURCES.md)

Phlox can query supported public data APIs through `query_public_api`, including APIs
whose documented search operation requires POST. The first adapter is **NIH RePORTER
parent-project search** (`nih_projects`). It accepts organization name fragments and
fiscal years. It works in ordinary Chat with **Web search enabled**, and in opt-in Research, subject to assistant tool
access and Tool Manager policy. No API key, package installation, configuration change,
database migration or separate service is required.

## Try it

Select Research with Web sources (or enable Web search in ordinary Chat), then ask:

> Use the NIH RePORTER public API tool to inspect projects matching Johns Hopkins for
> fiscal year 2024. Request two records per page and read only the first two pages.
> Show the project IDs, actual organization names, years and award amounts with citations.
> State how many matches the API reports and whether more pages remain. Do not calculate
> annual funding totals from this sample.

The agent starts with `org_names`, `fiscal_years`, and optionally `limit`. For the next
page it supplies **only `continue_from` and the previous page's S-label**, plus the optional
adapter name. Phlox reuses the saved filters, selected fields, sort and page size, and
advances the saved offset. The model cannot supply a different URL, POST body, headers,
credentials or an arbitrary offset. Each call sends one request; there is no automatic
bulk download, redirect following or retry loop.

Click a citation to inspect the returned fields and record range. **Retrieval query**
shows the exact payload. Opening the API endpoint in a browser does not repeat a POST
query. Citation inspection, saved-passage rereads and Markdown exports preserve the query
and captured evidence without accessing the API again. Separate queries/pages do not
produce false "changed source" notices merely because they share an endpoint.

## Validation and boundaries

- Queries require 1–5 organization fragments and 1–10 fiscal years (1985–2100). Empty
  filters, wildcard syntax and unsupported fields are rejected before dispatch.
- One request returns 1–20 records (default 5). A page's selected fields must fit a single
  6,000-character passage; oversized pages fail explicitly and require a new query with
  a smaller page size. Records are never silently dropped to fit.
- Returned year/name filters, parent-project status, required field types, page offsets,
  page sizes and counts are checked before capture. Application IDs must be unique and
  ascending, including across successive pages. A changing reported total, repeated
  page or inconsistent response stops continuation without capturing unvalidated rows.
- Captures preserve original numeric spellings and null amounts, organization identifiers
  when supplied, and administering/funding-agency fields. Other fields are omitted.
  Missing amounts are unknown, not zero. Name fragments can match multiple organizations;
  they are not legal-entity verification. RePORTER includes non-NIH agency projects too.
- Pagination ends at the API-reported total or its supported offset window (14,999).
  Hitting that window is reported as incomplete and requires narrower filters. Offset
  pagination is not a frozen database snapshot: stable counts/order cannot prove the
  upstream dataset stayed unchanged or that all relevant awards were returned.
- **A captured page is not a verified annual total.** Entity matching, fiscal-year
  completeness, funding-agency scope, monetary definitions, cross-query deduplication and
  multi-year aggregation still require analysis. This slice does not create datasets,
  charts, workspace scripts or download manifests.

Invalid responses expose a tool error without a new evidence snapshot or usable cursor.
To continue, the source must still be retained and belong to this conversation. Research
additionally requires the same attempt and allowed source domains. Deleted/expired
citations cannot authorize pagination. Starting a new Research attempt requires a fresh
query; it does not import an old attempt's pages.

## Policy and operations

The adapter is a registered read tool and defaults to automatic permission. Administrators
can disable it or require approval in the Tool Manager. Research includes it only for
Web or mixed source scope; domain restrictions must allow `api.reporter.nih.gov` (for
example, `nih.gov`). Every attempted query page consumes one source read, including errors,
under the existing Research allowance. Saved source capacity is checked before dispatch.
Current restrictions and counters survive approval resume and durable-run replay.

The adapter targets only `https://api.reporter.nih.gov/v2/projects/search`. POST is treated
as a read because this specific search endpoint and request schema are implemented in
Phlox, not because POST responses happen to contain data. Adding other adapters requires
code review of their endpoint, parameters, response validation and pagination semantics.
Generic `web_fetch` remains GET-only; other POST APIs remain unsupported by Research.

Requests use the existing DNS-pinned connection and private-network policy, with no
cookies, credentials or environment proxies. The 2 MiB response limit and 30-second
deadline cover pacing, download and isolated JSON parsing; Stop interrupts waiting,
network reads and parsing. Requests are paced at one start per second within Phlox's
supported single-process deployment. Compression, redirects and non-JSON responses are
rejected. HTTP failures are reported without response bodies or automatic retries.

The live API was checked with two one-record pages through the Phlox adapter. Automated
tests use local synthetic data for pagination, filter rejection, private access, Stop,
approval resume, notebooks and durable replay; these do not establish live-model research
quality or statistical completeness.

API reference: [official NIH RePORTER API documentation](https://api.reporter.nih.gov/).
