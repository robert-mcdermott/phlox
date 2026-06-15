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
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        {THEMES.map((t) => (
          <button
            key={t.id}
            onClick={() => setTheme(t.id)}
            className={`relative overflow-hidden rounded-xl border-2 p-3 text-left transition ${
              theme === t.id ? 'border-accent' : 'border-border hover:border-muted'
            }`}
          >
            {theme === t.id && (
              <span className="absolute right-2 top-2 rounded-full bg-accent p-0.5 text-accent-fg">
                <Check size={12} />
              </span>
            )}
            <div className="mb-2 flex gap-1">
              {t.swatch.map((c) => (
                <span key={c} className="h-6 w-6 rounded-full border border-black/10" style={{ background: c }} />
              ))}
            </div>
            <div className="text-sm font-medium text-content">{t.name}</div>
            <div className="text-xs text-muted">{t.dark ? 'Dark' : 'Light'}</div>
          </button>
        ))}
      </div>
    </div>
  )
}
