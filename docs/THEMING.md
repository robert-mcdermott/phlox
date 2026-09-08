# Theming

[User Guide](USER_GUIDE.md) · [Project overview](../README.md)

Phlox ships the **Phlox Dark** look by default and lets users switch themes at
runtime. Theming is a **CSS-variable token layer** under Tailwind, so themes change
instantly with no rebuild.

## Choose a theme

Open **Settings → Appearance** and select a theme. Each card previews its header,
sidebar, message bubble, and accent colors. The selection applies immediately and
is saved in your user settings and this browser. Existing theme choices are preserved;
**Phlox Dark remains the default**.

The 18 built-in themes cover bright, neutral, pastel, and neon palettes:

| Theme | Palette |
|---|---|
| Phlox Dark | Deep purple and magenta; the default |
| Phlox Light | Pale lavender and magenta |
| Fred Hutch | Navy, cyan, and gold on light surfaces |
| Light | White and teal |
| Dark | Slate and cyan |
| Hutch Night | Dark navy, purple, and cyan |
| Sandstone | Warm cream and gold |
| Terminal | Black and green |
| Outrun | Sunset orange, hot pink, and cyan over violet |
| Blade Runner 2049 | Deep teal, electric cyan, and amber |
| Chaos Theory | Carbon, acid green, and ice blue |
| Cyberpunk 2077 Blue | Midnight blue, cyan, hot pink, and yellow |
| Cyberpunk 2077 Violet | Black plum and electric fuchsia |
| Synthwave | Hot pink, ultraviolet, and cyan |
| Catppuccin Mocha | Soft lavender and pastel blue |
| Gruvbox Dark | Warm charcoal, gold, and orange |
| Nord | Arctic slate and muted frost |
| Monokai | Olive charcoal, lime, cyan, and pink |

The ten additions from Outrun through Monokai adapt
[Collomia's theme palettes](https://github.com/robert-mcdermott/collomia/blob/85063c9455cba29a83354c47cd9a8af949a6b606/internal/tui/theme.go).
Phlox needs more surface, text, and message-bubble colors than a terminal interface,
so these are adaptations rather than exact ports. Additional colors draw on the
[Catppuccin palette](https://catppuccin.com/palette/),
[Gruvbox color table](https://github.com/morhetz/gruvbox-contrib/blob/master/color.table),
and [Nord palette](https://www.nordtheme.com/docs/colors-and-palettes/).
Text and selected surfaces are adjusted for readability; Synthwave's pink accent
is lighter than Collomia's. The new palettes target at least 4.5:1 contrast for
primary, muted, and accent text on their main surfaces, plus button and message-bubble
text. This is a token-level check, not an accessibility certification of the entire UI.

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
3. **Catalog + apply** — `frontend/src/theme/presets.js` lists themes (id, name, optional description, swatch)
   and `applyTheme(id)` sets `data-theme` on `<html>` and saves the choice to
   `localStorage`. The Zustand store also persists it to backend settings.
4. **Switcher** — `components/settings/ThemeSwitcher.jsx` (Appearance tab) renders the
   catalog and calls `setTheme`. Its miniature UI previews use a nested `data-theme`
   attribute so each card renders the actual tokens without changing the active theme.

The fixed brand palette (`phlox`, plus the legacy `hutch-navy`, `hutch-cyan`,
`hutch-purple`, `hutch-gold`) is always available as Tailwind colors regardless of theme —
use these for brand-locked elements such as logos. Use the **semantic** tokens for
controls that should adapt to the theme. The "New chat" button uses `bg-accent` and
`text-accent-fg`, so both its background and label follow the selected theme.
Accent foregrounds use dark text where needed for contrast on brighter colors.

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
   { id: 'ocean', name: 'Ocean', description: 'Deep blue and sea green', swatch: ['#06283d', '#41d3bd', '#e6f2fb'], dark: true },
   ```

Done — it appears in the Appearance picker and applies instantly. **Define all token
variables** in every theme so no color falls back to an unset value.
Check text against every surface where it appears, including accent text, button
labels, and user-message bubbles. Set `color-scheme: dark` or `light` in the theme
block to match native browser controls.

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
