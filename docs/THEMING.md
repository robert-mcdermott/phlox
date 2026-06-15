# Theming

Phlox ships the **Phlox Dark** look by default and lets users switch themes at
runtime. Theming is a **CSS-variable token layer** under Tailwind, so themes change
instantly with no rebuild.

## How it works

1. **Tokens** — semantic colors in `frontend/tailwind.config.js` resolve to CSS variables:
   ```js
   colors: { bg: 'var(--color-bg)', surface: 'var(--color-surface)',
             accent: 'var(--color-accent)', content: 'var(--color-text)', ... }
   ```
   So `bg-surface`, `text-content`, `text-accent`, etc. follow the active theme.
2. **Theme blocks** — `frontend/src/theme/tokens.css` defines one block per theme keyed by
   the `data-theme` attribute on `<html>`:
   ```css
   [data-theme='phlox-dark'] { --color-bg:#160821; --color-accent:#df00ff; ... }
   ```
3. **Catalog + apply** — `frontend/src/theme/presets.js` lists themes (id, name, swatch)
   and `applyTheme(id)` sets `data-theme` on `<html>` and saves the choice to
   `localStorage`. The Zustand store also persists it to backend settings.
4. **Switcher** — `components/settings/ThemeSwitcher.jsx` (Appearance tab) renders the
   catalog and calls `setTheme`.

The fixed brand palette (`phlox`, plus the legacy `hutch-navy`, `hutch-cyan`,
`hutch-purple`, `hutch-gold`) is always available as Tailwind colors regardless of theme —
use these for brand-locked elements (e.g. the "New chat" button). Use the **semantic**
tokens for everything that should adapt to the theme.

## Add a theme

1. Add a block to `tokens.css` defining every `--color-*` variable (copy an existing
   block and tweak):
   ```css
   [data-theme='ocean'] {
     --color-bg: #06283d;  --color-surface: #0a3a55;  --color-surface-2: #0e4767;
     --color-surface-3: #135680; --color-border: #1b6a99;
     --color-text: #e6f2fb; --color-text-muted: #8fb6d1;
     --color-accent: #41d3bd; --color-accent-fg: #03202f;
     --color-user-bubble: #0e7490; --color-user-bubble-fg: #ecfeff;
     --color-header: #04202f; --color-header-border: #41d3bd;
     --color-sidebar: #04202f; --color-sidebar-fg: #cfe6f5;
   }
   ```
2. Add an entry to `THEMES` in `presets.js`:
   ```js
   { id: 'ocean', name: 'Ocean', swatch: ['#06283d', '#41d3bd', '#e6f2fb'], dark: true },
   ```

Done — it appears in the Appearance picker and applies instantly. **Define all token
variables** in every theme so no color falls back to an unset value.

## Token reference

| Variable | Used for |
|---|---|
| `--color-bg` | app background |
| `--color-surface` / `-2` / `-3` | cards, inputs, nested panels |
| `--color-border` | borders, dividers |
| `--color-text` / `--color-text-muted` | primary / secondary text |
| `--color-accent` / `--color-accent-fg` | buttons, links, highlights + their text |
| `--color-user-bubble` / `-fg` | user message bubble |
| `--color-header` / `--color-header-border` | top header bar + its accent border |
| `--color-sidebar` / `--color-sidebar-fg` | conversation sidebar |
