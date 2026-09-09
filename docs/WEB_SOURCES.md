# Web research and captured sources

[User Guide](USER_GUIDE.md) · [Source identity and retention](SOURCES.md)

Phlox can retain the passages it reads from web pages and attach clickable citations to
answers. This works in both chat modes, including opt-in reconnectable runs. Wave 8 uses
the existing source tables: no new migration, database, service, or feature flag is needed.
The current schema remains `0005_ingestion`. Restart the backend and rebuild the frontend
for production; document reprocessing or embedding rebuilds are not required for this wave.

## Use it

Enable **Search the web** in the composer to discover sources, or give Phlox a specific
URL and ask it to read the page and cite supporting passages. Tool availability still
depends on your assistant's capabilities and Tool Manager permissions. For example:

> Read these two public pages, compare their recommendations, and cite the passages you
> actually read. Say when a page could not be fetched.

Search results contain titles, URLs, and snippets marked **discovery**. They are not
registered page evidence. `web_fetch` reads HTML/text and returns captured passages with
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

## Failures, Stop, and recovery

HTTP errors, failed connections, denied private targets, oversized downloads, unsupported
formats, empty pages, and detected access barriers produce explicit tool errors. A private
failure record can identify the URL, attempt time, and reason, but has **no supporting
passage**. If the model references it, the panel and export show unavailable evidence.
Invalid URLs containing credentials are rejected without creating a source record.

Some access barriers return HTTP 200. Phlox detects an explicit paywall marker and a small
set of common short login/subscription/challenge messages, reporting a **possible** barrier.
This is heuristic; it cannot identify every paywall or certify that extraction is complete.
There is no paywall bypass, authenticated browsing, JavaScript execution, or OCR. PDF/DOCX
URLs are rejected by web fetch; download and upload those files through Documents instead.

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
fetcher still validates the returned content type and only extracts supported HTML/text.
DuckDuckGo requests use the `ddgs` library's own browser identity handling. A browser
User-Agent can improve compatibility, but does not execute JavaScript or provide a logged-in
browser session; sites may still return HTTP 403.

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
their existing separate implementation; this transport governs `web_fetch` only.

| Bound | Value |
|---|---|
| URL | 2,048 characters after normalization |
| Redirects | 5 |
| Response body | 2 MiB; enforced while reading, including without Content-Length |
| Fetch deadline / socket operation timeout | 30 seconds / 3 seconds |
| Concurrent unfinished OS DNS lookups | 4; additional lookups report busy |
| HTML nesting | 128 elements |
| Extracted text supplied per page | First 20,000 characters; truncation is explicit |
| Retained passage | 6,000 characters; up to four passages for one page |
| Shared source bounds | 64 per turn; 512 identities per conversation |

Limits are code constants, not new configuration settings. This is bounded HTML/text
extraction, not a browser renderer or semantic claim verifier. Research planning, source
filters, scheduled research, authenticated sites, and artifact-version citations remain
future waves.
