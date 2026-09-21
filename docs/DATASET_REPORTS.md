# Dataset analysis and HTML reports

[API datasets](API_DATASETS.md) · [Public APIs](PUBLIC_API.md) · [Research](RESEARCH.md)

Phlox can turn retained NIH RePORTER, PubMed or ClinicalTrials.gov query pages into a
self-contained HTML report with tables, bar charts and reproducible data files. It uses
validated source records, without refetching pages, installing packages or executing
model-generated code. Ordinary Chat needs **Web search** enabled; Research remains opt-in.

## Workflow

1. Query a small page with `query_public_api` and check the filters. Retrieve further pages
   with `continue_from`, or use `collect_api_dataset` for bounded collection.
2. `analyze_api_dataset` takes the retained query-page `labels` and returns column names,
   types, missing counts and dataset coverage. Nested scalar fields use dotted paths,
   such as `organization.org_name`. Raw records stay out of this inspection response.
3. `create_api_report` takes those labels, a title and analysis sections. Each section
   counts records or sums a numeric quantity, optionally grouped by a column. Filters
   select the records to analyze. The tool creates real files before final synthesis.

Inspection is read-only and defaults to **Auto**. Report creation defaults to **Ask**;
Agent mode and Tool Manager overrides work as usual. Selecting Research does not bypass
file approval. Report creation also requires dataset export to be enabled and not denied.
Read-only sub-agents can inspect, but cannot create reports.

## Files

Every report is published in a new conversation workspace folder:

| File | Contents |
|---|---|
| `report.html` | Standalone report with coverage, exact tables, proportional bar charts, method and source references |
| `analysis.csv`, `analysis.json` | Computed groups, counts/sums, missing values and selections |
| `records.csv`, `records.json` | Original captured dataset, before report filters |
| `summary.csv` | The adapter's existing dataset coverage/summary |
| `manifest.json` | Query and analysis recipes, source capture times, ranges, hashes, coverage and verification method |

Open the `report.html` file card in the existing preview or download it. It loads no
scripts, external fonts, images or live data. The exact table is the accessible alternative
to each chart. All groups appear in the table; charts show at most the first 20, with a
notice when more exist. Saved answer copies survive refresh and later workspace edits.
A prior collection also creates its own four files; the report's seven files form a
separate bundle, preserving the earlier export.

## Analysis semantics and limits

- `labels`: 1–64 distinct query-page source labels from one query and supported adapter.
  Article/study detail passages are not tabular query rows and cannot be included here.
- `sections`: 1–6 objects with a required `title`, optional `group_by`, `metric` (`count`
  by default, or `sum`), and `value_field` for a sum. Omitting `group_by` analyzes all
  selected records together. At most 50 groups per section; narrow the selection if needed.
- `filters`: up to eight `{field, op, value}` rules, all ANDed. Operators are `equals`,
  `contains`, `minimum` and `maximum`. Values are strings, including exact decimal text.
  Equality is exact; contains is case-insensitive. Boolean equality uses `true` or `false`.
  List filters match any element; numeric bounds require a scalar numeric column.
- Scalar strings, numbers, booleans and lists of scalar values are supported. Arrays of
  objects are omitted. At most 64 columns are inspected; no automatic date parsing, joins,
  inferred organization reconciliation or arbitrary expressions are provided.
- Each record counts once within each group. Lists such as trial phases can put a record
  in several groups: an explicit notice explains why group counts can exceed unique records.
  Sums over list groupings are rejected to prevent double counting.
- Numeric sums use exact decimal arithmetic over known values. Missing amounts remain
  unknown; an entirely missing sum is null, not zero. Use meaningful quantities rather
  than dates or identifiers; the adapter's primary record identifier cannot be summed.
  Numeric input bounds remain 100 significant digits and exponents between −100 and 100.
- Partial capture is prominently labelled. Report filters do not change the API's reported
  match count or original coverage. NIH sums are **not verified NIH-only annual funding**;
  PubMed metadata does not establish study findings, and trial registration is not evidence
  of efficacy. The existing adapter notices remain in the report.

The full bundle is limited to **2 MiB**. Files are staged, checked against their generated
bytes, and published together. Tables, chart labels and analysis files share the computed
results. The manifest explicitly says that live visual review was **not** performed at
generation time. Phlox does not start a preview server or claim a browser inspection occurred.

Research offers inspection and reporting after an API page is captured, with at most four
inspection attempts and two report attempts per turn. They consume no new search/read
allowance and remain available when reads or source storage are full. Time, reported-token
and model-pass ceilings still apply. Requested files must be created during gathering,
before tool-free synthesis; instructions prioritize this, but a general task-completion
tracker and arbitrary code/report execution remain future work.

Sources are reauthorized for ownership, expiry, current Research attempt and domain scope,
including immediately before publication. Source removal or Stop prevents new publication;
a forced process kill can leave an unreported bundle and is never automatically replayed.
Previously saved reports are independent copies: later source removal does not erase them.
In ordinary Chat, earlier owned citations can be reused while available. A new Research
attempt retains its fresh-evidence boundary. Workspace edits are never accepted as analysis
input by these tools. No new configuration, dependency or database migration is required.

## Manual verification

Choose **Research → Web → Standard**, with Agent mode off to see file approvals. Submit:

> Use query_public_api with adapter clinical_trials to find ovarian cancer trials with
> statuses RECRUITING and limit 2. Inspect that preview, then use collect_api_dataset with
> max_pages 2 and max_records 6. Stop acquiring after those pages. Use analyze_api_dataset
> to inspect the retained query columns, then create_api_report titled "Recruiting ovarian
> cancer trials: a small registry sample", with count sections grouped by lead_sponsor
> and phases. Include source references and clearly explain partial coverage and overlapping
> phase categories. Deliver report.html and its data files in this request. Do not fetch
> study details or infer treatment effectiveness.

1. Approve collection and report creation when prompted. With at least six matches, expect
   three page reads, a collection bundle and a separate seven-file report bundle.
2. Open `report.html`. Check its captured/selected count against `records.json`, its match
   count against the API result, and its partial-dataset notice when more matches remain.
3. Compare the sponsor table and bar labels with `analysis.csv`. Phase counts may overlap;
   each distinct phase counts a study once. Missing categories appear as “Not reported.”
4. Inspect the manifest's source labels, query and analysis recipes. Refresh the conversation
   and confirm the saved report still opens/downloads.
5. In a fresh conversation, deny report creation. No report bundle should appear. Previously
   collected source records and collection files remain available.

For exact sums, query a small NIH sample and request a report grouped by `fiscal_year`,
using `metric=sum` and `value_field=award_amount`. Describe it as the sum of known awards
in the captured sample, never an annual institutional funding total. For PubMed, group
bibliographic records by `journal` or count author memberships; dates remain source strings.
