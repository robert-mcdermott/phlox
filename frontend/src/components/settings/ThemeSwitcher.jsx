import { Check } from 'lucide-react'
import { THEMES } from '../../theme/presets'
import { useStore } from '../../store/useStore'

export default function ThemeSwitcher() {
  const theme = useStore((s) => s.theme)
  const setTheme = useStore((s) => s.setTheme)

  return (
    <div>
      <h3 className="mb-1 text-sm font-semibold text-content">Theme</h3>
      <p className="mb-4 text-xs text-muted">
        Phlox Dark is the default. Themes apply instantly and are remembered on this device.
      </p>
      <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 10rem), 1fr))' }}>
        {THEMES.map((t) => (
          <button
            key={t.id}
            type="button"
            aria-label={`${t.name} theme`}
            aria-pressed={theme === t.id}
            onClick={() => setTheme(t.id)}
            className={`relative overflow-hidden rounded-xl border-2 p-3 text-left transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent ${
              theme === t.id ? 'border-accent' : 'border-border hover:border-muted'
            }`}
          >
            {theme === t.id && (
              <span className="absolute right-2 top-2 z-10 rounded-full bg-accent p-0.5 text-accent-fg" aria-hidden="true">
                <Check size={12} />
              </span>
            )}
            <div data-theme={t.id} aria-hidden="true" className="mb-3 overflow-hidden rounded-md border border-border bg-bg">
              <div className="h-3 border-b-2" style={{ background: 'var(--color-header)', borderColor: 'var(--color-header-border)' }} />
              <div className="flex h-16">
                <div className="w-1/5 space-y-1 p-1.5" style={{ background: 'var(--color-sidebar)' }}>
                  <div className="h-1 w-full rounded bg-accent" />
                  <div className="h-1 w-2/3 rounded" style={{ background: 'var(--color-sidebar-fg)', opacity: 0.5 }} />
                </div>
                <div className="flex-1 space-y-1 p-2">
                  <div className="ml-auto w-3/4 rounded bg-user-bubble px-1.5 py-0.5 text-[9px] text-user-bubble-fg">Hello, Phlox</div>
                  <div className="w-5/6 rounded bg-surface px-1.5 py-1">
                    <div className="h-1 w-full rounded bg-content opacity-80" />
                    <div className="mt-1 h-1 w-2/3 rounded bg-accent" />
                  </div>
                </div>
              </div>
            </div>
            <div className="text-sm font-medium text-content">{t.name}</div>
            <div className="mt-1 text-xs text-muted">{t.description || (t.dark ? 'Dark' : 'Light')}</div>
          </button>
        ))}
      </div>
    </div>
  )
}
