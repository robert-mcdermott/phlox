// Theme catalog. The `id` must match a [data-theme="id"] block in tokens.css.
// `swatch` summarizes the palette; picker previews use the theme's CSS tokens.
export const THEMES = [
  { id: 'phlox-dark', name: 'Phlox Dark', swatch: ['#160821', '#DF00FF', '#f0e6f7'], dark: true },
  { id: 'phlox-light', name: 'Phlox Light', swatch: ['#faf5fe', '#C200DE', '#2a1438'], dark: false },
  { id: 'fred-hutch', name: 'Fred Hutch', swatch: ['#1B365D', '#00ABC8', '#FFB500'], dark: false },
  { id: 'light', name: 'Light', swatch: ['#ffffff', '#0ea5b7', '#111827'], dark: false },
  { id: 'dark', name: 'Dark', swatch: ['#0f172a', '#22d3ee', '#e2e8f0'], dark: true },
  { id: 'hutch-night', name: 'Hutch Night', swatch: ['#10192b', '#AA4AC4', '#00ABC8'], dark: true },
  { id: 'sandstone', name: 'Sandstone', swatch: ['#faf5ee', '#b8860b', '#1B365D'], dark: false },
  { id: 'terminal', name: 'Terminal', swatch: ['#000000', '#00ff41', '#00ff41'], dark: true },
  { id: 'outrun', name: 'Outrun', description: 'Sunset orange, cyan and violet', swatch: ['#1a0e31', '#ff6c11', '#ff3864'], dark: true },
  { id: 'blade-runner-2049', name: 'Blade Runner 2049', description: 'Deep teal and electric amber', swatch: ['#001f1f', '#00ffe5', '#ffb74d'], dark: true },
  { id: 'chaos-theory', name: 'Chaos Theory', description: 'Carbon, acid green and ice blue', swatch: ['#0d0d0d', '#61f21d', '#0fc9f2'], dark: true },
  { id: 'cyberpunk-2077-blue', name: 'Cyberpunk 2077 Blue', description: 'Midnight blue, cyan and hot pink', swatch: ['#03102c', '#0ef3ff', '#ff2e97'], dark: true },
  { id: 'cyberpunk-2077-violet', name: 'Cyberpunk 2077 Violet', description: 'Black plum and electric fuchsia', swatch: ['#120018', '#ff2cf1', '#c832ff'], dark: true },
  { id: 'synthwave', name: 'Synthwave', description: 'Hot pink, ultraviolet and cyan', swatch: ['#191325', '#ff64b0', '#b967ff'], dark: true },
  { id: 'catppuccin-mocha', name: 'Catppuccin Mocha', description: 'Soft lavender and pastel blue', swatch: ['#1e1e2e', '#cba6f7', '#89b4fa'], dark: true },
  { id: 'gruvbox-dark', name: 'Gruvbox Dark', description: 'Warm charcoal, gold and orange', swatch: ['#282828', '#fabd2f', '#fe8019'], dark: true },
  { id: 'nord', name: 'Nord', description: 'Arctic slate and muted frost', swatch: ['#242933', '#88c0d0', '#81a1c1'], dark: true },
  { id: 'monokai', name: 'Monokai', description: 'Olive charcoal, lime and cyan', swatch: ['#1d1e19', '#66d9ef', '#a6e22e'], dark: true },
]

export const DEFAULT_THEME = 'phlox-dark'

export function applyTheme(id) {
  document.documentElement.setAttribute('data-theme', id)
  try {
    localStorage.setItem('phlox-theme', id)
  } catch {
    /* ignore */
  }
}

export function initialTheme() {
  try {
    return localStorage.getItem('phlox-theme') || DEFAULT_THEME
  } catch {
    return DEFAULT_THEME
  }
}
