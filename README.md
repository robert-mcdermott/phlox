# Phlox product website

This branch contains the static site at <https://robert-mcdermott.github.io/phlox/>.
GitHub Pages publishes the root of `gh-pages`. Pushing this branch updates the public
website; local edits and previews do not publish it.

## Local preview

No build or npm dependencies are needed. From this checkout:

```bash
uv run --no-project -m http.server 5189 --bind 127.0.0.1
```

Open <http://127.0.0.1:5189>. Stop with Ctrl+C.

## Release updates

- Update the release link, quick-start tag, metadata, and release highlights in `index.html`.
  Bump the stylesheet URL version when changing CSS so returning visitors load the new styles.
- Link user-facing guides to the release tag so they match the advertised version. The
  contributor roadmap links to `main` intentionally.
- Check setup, defaults, upgrade steps, and operating limits against the release's
  `docs/USER_GUIDE.md`, `MODEL_DISCOVERY.md`, `PROJECTS.md`, `CONVERSATION_ALTERNATIVES.md`,
  `ARTIFACTS.md`, `RESEARCH.md`, `PUBLIC_API.md`, `API_DATASETS.md`, `DATASET_REPORTS.md`,
  `RUNS.md`, `BACKUP_RESTORE.md`, and `INGESTION.md`.
- Keep Research explicitly opt-in and normal Chat the default. Search administration lives
  in the admin console; DuckDuckGo remains the default and fallback, and public SearXNG
  endpoints must support JSON. Update upgrade advice for the specific previous release.
- `css/themes.css` contains the release's exact application palette values, scoped to
  `.theme-demo[data-preview-theme]`. Keep them aligned with the app's `tokens.css` and
  keep the 18 preview controls aligned with `presets.js`. The preview is illustrative;
  it does not run an agent or change the site's theme.
- Keep `.nojekyll` and the Google verification file. Screenshots are existing product
  captures; retain descriptive alt text and width/height attributes when replacing them.

## Verify before publishing

Check desktop, tablet, and mobile layouts, keyboard navigation (including Escape), all
theme selections, copy buttons, anchor/doc links, and page readability with JavaScript
unavailable or reduced motion enabled. Run `node --check js/main.js` and
`git diff --check`. The page should have no missing assets or horizontal overflow.

## v0.4.0 content

The release highlights focus on public-data research (NIH RePORTER, PubMed, and
ClinicalTrials.gov), bulk collection with reusable dataset checkpoints, downloadable
analysis and reports, and longer research tasks with inspectable evidence. The data
workflow is explanatory content, not a screenshot or a claim about a particular dataset.
Release documentation and clone commands are pinned to `v0.4.0`; the upgrade note includes
`0009_api_datasets` and points custom service operators to the updated deployment guide.

Preserve the product boundaries: Research remains opt-in; an analysis handoff makes eligible
execution tools available but does not bypass their permissions. Bulk collection has API,
storage, and administrator limits. Resuming a dataset is explicit and requires its sources
to remain available. Incomplete data must not be described as complete, and metadata or
registered studies are not evidence of scientific findings. The integrations do not cover
arbitrary APIs or full-text journal access. Reconnectable runs remain optional, with no
automatic replay of uncertain actions after interruption.

Projects, model discovery, conversation alternatives, and versioned artifact editing remain
prominent existing features. Saving an artifact is separate from **Use in workspace**;
switching conversation alternatives does not restore files. Self-hosting does not prevent
requests from reaching configured cloud model and search providers.

## Screenshot refresh

Existing product captures are used; the team administration section keeps the usage and
budget overviews. The former pricing, API-key, warning, and error screenshots remain in the
image directory but are no longer displayed in its gallery. The most valuable next capture is a v0.4.0
Research conversation beside its generated HTML report in the canvas: show a legible chart,
source citations, dataset coverage, and downloadable files. Use a small public-data example
with accurate labels and reviewed values, and omit credentials and private conversations.
This can illustrate the new data section and eventually replace the older hero capture.

Additional captures of the project/context controls and artifact editor would refresh those
sections. Until new captures are available, do not present the existing images as screenshots
of the v0.4.0 research workflow.
