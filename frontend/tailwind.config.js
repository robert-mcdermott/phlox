/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        // Fixed brand palette.
        phlox: '#DF00FF',
        'hutch-navy': '#1B365D',
        'hutch-cyan': '#00ABC8',
        'hutch-purple': '#AA4AC4',
        'hutch-gold': '#FFB500',
        // Semantic tokens driven by CSS variables (see src/theme/tokens.css).
        // These let themes change at runtime without a rebuild.
        bg: 'var(--color-bg)',
        surface: 'var(--color-surface)',
        'surface-2': 'var(--color-surface-2)',
        'surface-3': 'var(--color-surface-3)',
        border: 'var(--color-border)',
        content: 'var(--color-text)',
        muted: 'var(--color-text-muted)',
        accent: 'var(--color-accent)',
        'accent-fg': 'var(--color-accent-fg)',
        'user-bubble': 'var(--color-user-bubble)',
        'user-bubble-fg': 'var(--color-user-bubble-fg)',
      },
    },
  },
  plugins: [require('@tailwindcss/forms'), require('@tailwindcss/typography')],
}
