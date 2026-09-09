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
- Link user-facing guides to the release tag so they match the advertised version. The
  contributor roadmap links to `main` intentionally.
- Check setup, defaults, upgrade steps, and operating limits against the release's
  `docs/USER_GUIDE.md`, `MODEL_DISCOVERY.md`, `PROJECTS.md`, `CONVERSATION_ALTERNATIVES.md`,
  `ARTIFACTS.md`, `RESEARCH.md`, `RUNS.md`, `BACKUP_RESTORE.md`, and `INGESTION.md`.
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

## v0.3.0 content

The release highlights cover automatic model discovery, private projects and inspectable
context, conversation alternatives, and versioned artifact editing. Research and configurable
search remain prominent existing features. Release documentation and clone commands are pinned
to `v0.3.0`; the upgrade note includes migrations through `0008_artifacts`.

Preserve the distinctions in the focused guides: existing curated model lists need Automatic
explicitly enabled, switching conversation alternatives does not restore workspace files, and
saving an artifact version is separate from choosing **Use in workspace**. Unsaved artifact
drafts do not survive browser refresh. Ordinary Chat remains the default.

Existing screenshots are retained for this update. Future captures should illustrate the model
picker, project/context controls, conversation alternatives, and artifact editor. Until then,
new features are explained in text without presenting older screenshots as captures of them.
