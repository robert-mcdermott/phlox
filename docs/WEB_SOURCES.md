# Web research and captured sources

[User Guide](USER_GUIDE.md) · [Source identity and retention](SOURCES.md)

Phlox can retain the passages it reads from web pages and attach clickable citations to
answers. This works in both chat modes, including opt-in reconnectable runs. Wave 8 uses
the existing source tables: no new migration, database, service, or feature flag is needed.
The current schema is `0008_artifacts` (see [Editable artifacts](ARTIFACTS.md)). Restart the backend and rebuild the frontend
for production; document reprocessing or embedding rebuilds are not required for this wave.

## Use it

Enable **Search the web** in the composer to discover sources, or give Phlox a specific
URL and ask it to read the page and cite supporting passages. Tool availability still
depends on your assistant's capabilities and Tool Manager permissions. For example:

> Read these two public pages, compare their recommendations, and cite the passages you
> actually read. Say when a page could not be fetched.

Search results contain titles, URLs, and snippets marked **discovery**. They are not
registered page evidence. `web_fetch` reads HTML/text, public PDFs, and JSON responses, returning captured passages with
stable labels such as **[S1]**. The model chooses which passages to cite; a valid label
does not establish that the passage supports the claim.

Click a citation to see its title, original URL, fetch time, retained passage, and character
range within the extracted text. **Open original page** opens a new tab; it may show newer
content. Opening the source panel itself never re-fetches the site. A notice appears when
another retained capture of the same URL has different extracted content. Neither version
is automatically treated as more authoritative.

Long pages are split into bounded citation passages. Re-fetching identical text at the
same normalized URL and passage offset reuses its label and updates the fetch time.
Changed content receives new labels. URL fragments and default ports are normalized;
tracking parameters and unrelated URLs are not assumed equivalent. The content hash covers
the extracted page text, not the original HTML bytes. Extraction can omit page layout.

## Focused reading and saved evidence

Phlox can now look beyond the first 20,000 characters of a page. Ask it to find a specific
section or figure; `web_fetch` accepts optional `query` keywords and selects a contiguous
passage of at most 6,000 characters from the extracted page. Matching is lexical, not a
guarantee of relevance or completeness. No match is reported explicitly without creating
a failed-page citation. Navigation elements and site headers/footers outside main/article
content are omitted; article headings and table cell boundaries are preserved.

For sequential reading, `start_char` selects a zero-based extracted-text offset and
`max_chars` limits the response to 1–20,000 characters. Results identify the selected range,
total extracted length, and the next offset when text follows. Only the supplied passages
are captured; the rest is not stored. Every fetch still downloads the current page within
the existing 2 MiB bound. Offsets can move if a site changes between calls, and changed
content receives separate citation identities.

`read_web_source` rereads a retained citation such as `S3` without contacting the website.
It returns the original captured passage and date, keeps its label, and does not extend
retention. Normal Chat can reuse accessible web evidence from the same conversation;
Research can reread only evidence captured for its current attempt and allowed domains.
Removed, expired, failed, foreign, and out-of-scope sources remain unavailable. Research
counts these operations against its source-read allowance, just like page fetches.

Research also has a [working notebook](RESEARCH.md#research-notebook-and-working-context).
Accepted source-linked notes allow earlier tool exchanges to be condensed in model input.
For final writing, Phlox restores the original referenced passages that are still accessible
and fit the context allowance. This preparation makes no additional read tool calls or
network requests, creates no sources, and never renews retention. Missing/omitted passages
are explicit. Models must update the notebook to use this reduction; no full-page cache
is introduced.

To check the feature manually:

1. Give Phlox a long public HTML report and ask it to find a specific table using a
   keyword-focused fetch. Inspect the tool arguments and citation panel for the selected
   passage and its character range; the range can now begin beyond character 20,000.
2. Ask it to read the following section using the returned next offset. Export the answer
   and confirm the citations include the selected passages and their original locations.
3. In normal Chat, ask it to use only `read_web_source` to reread one of those labels.
   The tool result should identify the retained capture date, with no new page fetch.
4. Remove that snapshot in the citation panel and repeat the retained-only read. The tool
   must report it unavailable. A new explicit fetch is needed to capture it again.

## PDF and JSON sources

Public PDFs with a text layer work through the same `web_fetch` tool. Ask for a report or
table and its page citations. The model can search extracted text with `query`, page
through it with `start_char`, or select one **`pdf_page`** (one-based). With `pdf_page`,
query/offset selection is within that page. Without it, offsets refer to the concatenated
nonempty page text. Each retained passage stays within one PDF page, and the source panel,
saved rereads, and Markdown export identify the page and its local character offsets.
Layout extraction preserves basic columns and line breaks; complex tables, charts, rotated
text and reading order still need verification against the original. Pages without
extractable text are listed; there is no OCR or external image-decoder execution.

For JSON, Phlox retains **complete values or complete array records** rather than chopping
text at an arbitrary character. Nested fields, arrays, null/boolean values and original
number spellings are preserved. Use **`json_pointer`** to select a value, such as `/results`
or `/results/0/amount`. An empty pointer selects the root. In keys, escape `~` as `~0` and
`/` as `~1`. A selected array supports **`json_start`** (zero-based, default 0) and
**`json_limit`** (default 20, maximum 50). The complete selection must fit within 6,000
characters or the smaller requested `max_chars`. The result gives the retained index
range, total array length and next index when more items remain.

If a whole object or one array record is too large, the model receives an explicit
selection error, with a bounded field/type preview for objects, and can request a smaller
JSON path. That preview is navigation data, not captured evidence. JSON does not use
`query`, `start_char`, or `pdf_page`. Duplicate object keys, nonstandard constants, malformed
JSON and excessive nesting are rejected. JSON schema responses are readable as data;
external `$ref` links are never followed automatically.

Array selection operates on **one downloaded response**. It does not follow API pagination,
submit POST bodies, supply credentials, prove that server-side filters were honored, or
infer that omitted records are absent. Each subsequent selection downloads the current
response again, so records/indices can change between reads. For supported POST queries,
the separate [public API tool](PUBLIC_API.md) now provides a scoped NIH RePORTER adapter
with validated pages and saved-query continuation. Bulk datasets remain later work.

PDF and JSON passages use the existing web-source permissions, ownership, retention and
source limits. Research notebook findings can reference their citations; final synthesis
restores their complete retained excerpts and provenance without another HTTP request.
Only passages are saved, not the original PDF/JSON file. Removing a snapshot or its expiry
prevents future retained reads, as for HTML evidence.

To test manually, supply a public PDF URL and a public JSON URL in a Research request.
Ask it to quote a figure from a specific PDF page and extract a small range of JSON records,
update the notebook, and produce a cited comparison. Open each citation to verify the
page or JSON pointer/index range. Refresh the chat and inspect it again. Try an encrypted
or scanned PDF separately: the response should explain the limitation rather than claim
it captured usable evidence. No new admin configuration or database migration is needed.

## Failures, Stop, and recovery

HTTP errors, failed connections, denied private targets, oversized downloads, unsupported
formats, empty pages, and detected access barriers produce explicit tool errors. A private
failure record can identify the URL, attempt time, and reason, but has **no supporting
passage**. If the model references it, the panel and export show unavailable evidence.
Invalid URLs containing credentials are rejected without creating a source record.

Some access barriers return HTTP 200. Phlox detects an explicit paywall marker and a small
set of common short login/subscription/challenge messages, reporting a **possible** barrier.
This is heuristic; it cannot identify every paywall or certify that extraction is complete.
There is no paywall bypass, authenticated browsing, JavaScript execution, or OCR. Public
PDF URLs with a text layer are supported. DOCX URLs remain unsupported; upload those files
through Documents instead. Encrypted PDFs and scanned pages without extractable text are
reported explicitly, without inventing evidence from a successful HTTP status.

Chat **Stop** interrupts an active fetch and prevents new evidence publication after
cancellation is observed. Network reads/connections have short timeouts and an overall
deadline; a bounded resolver task may finish its OS DNS lookup after the caller stops,
but it cannot open a connection. Previously captured evidence remains. With runs enabled,
closing a tab only detaches the viewer; use Stop to end work. See [RUNS.md](RUNS.md).

Registered citations survive reloads, approval pauses, durable event replay, and Markdown
exports. Restart does not automatically repeat an interrupted tool call. An external HTTP
request may already have reached its server when local cancellation occurs.

## Privacy and retention

Web snapshots belong to the conversation owner, even if their original page is public.
Copying a source ID cannot grant another user or admin access. Existing source limits and
30-day retention apply to document and web records together. Expired snapshots become
unavailable immediately on reads; existing cleanup purges retained text/URLs and query
context. See [SOURCES.md](SOURCES.md) for cleanup timing.

Use **Remove retained snapshot** in a web source panel to erase that citation's retained
passage and URL. Its stable label becomes unavailable. Other passages from the same page
are separate snapshots. A later explicit fetch can capture the passage again. Deleting
the conversation or the owner's data removes its source records.

Removal does not rewrite previous messages, tool results, pending context, run events,
downloaded exports, or backups. URLs can contain private query values; treat snapshots and
backups accordingly. Access changes at the original website are not automatically detected
and do not revoke an already retained snapshot.

## Fetch configuration and limits

Page fetching and the Serper/SearXNG search clients use a fixed desktop Chrome User-Agent,
matching Collomia. Its shared value is maintained in `backend/app/web_fetch.py`.
Page requests also match Collomia's weighted `Accept` header and `Accept-Language:
en-US,en;q=0.9` for compatibility with sites that reject minimal request headers. The
fetcher validates the returned content type: supported HTML/text, `application/pdf`,
`application/json`, and `application/*+json` (including JSON schemas).
DuckDuckGo requests use the `ddgs` library's own browser identity handling. A browser
User-Agent can improve compatibility, but does not execute JavaScript or provide a logged-in
browser session; sites may still return HTTP 403.
HTTPS negotiation matches Python's standard HTTPS client (HTTP/1.1 ALPN and TLS 1.3
post-handshake-auth capability where supported). This corrected a reproducible
ClinicalTrials.gov API rejection while preserving certificate and hostname checks;
it does not guarantee access to sites that restrict automation.

Existing file-only `web_fetch.allow_private_networks` and `web_fetch.allowlist_hosts`
settings remain in effect. By default, every resolved address must be public. Each redirect
is checked independently, and the HTTP connection uses a validated numeric address with
the original hostname for Host and HTTPS certificate verification. It cannot automatically
reconnect through a second hostname lookup. Explicit allowlists can permit intended internal
hosts; deprecated site-local and known IPv6 transition/translation ranges are blocked by
default too. CIDR entries apply to literal IP URLs, not arbitrary hostnames resolving into a CIDR.

Fetch sends no login cookies or Authorization header, rejects embedded URL credentials,
ignores environment proxies, and requests uncompressed content. A server that insists on
compression is rejected. Enterprise proxy-only networks need a future controlled proxy
integration; do not disable address checks as a workaround. Search-provider requests keep
their existing separate implementation. The reviewed [public API adapters](PUBLIC_API.md)
reuse the same connection checks and download bounds, with fixed request contracts and
no redirects. Those adapters have [bounded transient retries](PUBLIC_API.md#temporary-failures-and-automatic-retries)
within their shared deadline; ordinary `web_fetch` does not automatically retry.

| Bound | Value |
|---|---|
| URL | 2,048 characters after normalization |
| Redirects | 5 |
| Response body | 2 MiB; enforced while reading, including without Content-Length |
| Fetch deadline / socket operation timeout | 30 seconds / 3 seconds |
| Concurrent unfinished OS DNS lookups | 4; additional lookups report busy |
| HTML nesting | 128 elements |
| Extracted text supplied per fetch | Up to 20,000 characters from the requested offset; default starts at zero |
| Keyword-focused selection | Up to 6,000 contiguous characters; query up to 200 characters |
| Retained passage | 6,000 characters; HTML/text uses up to four per fetch; PDF page boundaries can create more |
| PDF extraction | Scan up to 200 pages, or select one `pdf_page` (1–10,000); up to 500,000 extracted characters |
| PDF stream expansion | 8 MiB per bounded decoder/stream, 32 MiB cumulative decoded streams |
| PDF/JSON parsing | Two concurrent subprocesses; shared 30-second fetch deadline and cancellation; 10 CPU seconds where supported, 512 MiB address space on Linux |
| JSON selection | One complete value or array slice, up to 6,000 characters; `json_limit` 1–50 items, default 20 |
| JSON structure | Depth 64; pointer up to 512 characters; duplicate keys and nonstandard constants rejected |
| Shared source bounds | 64 per turn; 512 identities per conversation |

Limits are code constants, not new configuration settings. This is bounded source
extraction, not a browser renderer or semantic claim verifier. Opt-in Research and domain/
document selection are available; see [RESEARCH.md](RESEARCH.md). Scheduled research,
authenticated sites, and artifact-version citations remain future work.
