# Exporting retained API datasets

[Public API queries](PUBLIC_API.md) · [Research](RESEARCH.md) · [User Guide](USER_GUIDE.md)

When a task asks for downloadable data, `export_api_dataset` can turn retained NIH
RePORTER or PubMed query pages into four base files in a new conversation workspace folder:

| File | Contents |
|---|---|
| `records.csv` | NIH project fields, or PubMed PMIDs, titles, authors, journals, dates and identifiers |
| `records.json` | All fields retained by the API adapter, including nested data and original numeric spellings |
| `summary.csv` | Dataset coverage, NIH counts/known award sums/missing amounts grouped by organization and fiscal year; PubMed captured and reported record counts |
| `record_details.json` (optional) | Captured abstract/author selections, with record IDs, versions, filters and ranges; only when `detail_labels` are supplied |
| `manifest.json` | Query recipe, source references/capture times, coverage and gaps, duplicate counts, and data-file hashes |

The tool reads existing source snapshots. It makes no network requests, installs nothing,
and runs no model-generated code. This is the first bounded Research-to-files workflow;
it does not enable arbitrary shell execution, chart creation or bulk API downloads.

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

## Validation and limits

The agent supplies 1–64 distinct query source `labels`, plus optional `detail_labels`, with
at most 64 distinct labels combined. It supplies no file contents or model-authored rows.
Detail IDs must occur in the selected query pages and use the same adapter. Mixed detail
versions of a record are rejected. Detail selections stay separate from query records and
do not change the query coverage statistics; absence from the detail file means unread,
not that an abstract/affiliation does not exist. Each selection retains its own provenance.
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

The complete bundle is limited to 2 MiB. They are staged privately and published
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
