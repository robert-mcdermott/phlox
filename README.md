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
  `docs/USER_GUIDE.md`, `RUNS.md`, `BACKUP_RESTORE.md`, and `INGESTION.md`.
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
