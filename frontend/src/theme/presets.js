// Theme catalog. The `id` must match a [data-theme="id"] block in tokens.css.
// `swatch` colors are only used to render the picker preview.
export const THEMES = [
  { id: 'phlox-dark', name: 'Phlox Dark', swatch: ['#160821', '#DF00FF', '#f0e6f7'], dark: true },
  { id: 'phlox-light', name: 'Phlox Light', swatch: ['#faf5fe', '#C200DE', '#2a1438'], dark: false },
  { id: 'fred-hutch', name: 'Fred Hutch', swatch: ['#1B365D', '#00ABC8', '#FFB500'], dark: false },
  { id: 'light', name: 'Light', swatch: ['#ffffff', '#0ea5b7', '#111827'], dark: false },
  { id: 'dark', name: 'Dark', swatch: ['#0f172a', '#22d3ee', '#e2e8f0'], dark: true },
  { id: 'hutch-night', name: 'Hutch Night', swatch: ['#10192b', '#AA4AC4', '#00ABC8'], dark: true },
  { id: 'sandstone', name: 'Sandstone', swatch: ['#faf5ee', '#b8860b', '#1B365D'], dark: false },
  { id: 'terminal', name: 'Terminal', swatch: ['#000000', '#00ff41', '#00ff41'], dark: true },
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
