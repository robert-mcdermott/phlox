import { useEffect, useState } from 'react'
import { FlaskConical, Loader2, Save } from 'lucide-react'
import { api } from '../../api/client'

const fields = [
  ['rounds', 'Model passes', 3, 100], ['searches', 'Searches', 1, 100],
  ['reads', 'Page reads', 1, 100], ['seconds', 'Gathering seconds', 30, 7200],
  ['tokens', 'Reported token threshold', 1000, 5000000],
]
const depths = { brief: 'Brief', standard: 'Standard', thorough: 'Thorough' }

export default function ResearchSettings({ config, onSaved }) {
  const [presets, setPresets] = useState(config)
  const [busy, setBusy] = useState(false)
  const [feedback, setFeedback] = useState('')
  useEffect(() => { setPresets(config) }, [config])
  const save = async (event) => {
    event.preventDefault()
    setBusy(true); setFeedback('')
    try {
      const body = Object.fromEntries(Object.entries(presets).map(([depth, limits]) => [depth,
        Object.fromEntries(fields.map(([key]) => [key, Number(limits[key])])),
      ]))
      const fresh = await api.updateAdminConfig('research', body)
      onSaved(fresh)
      setFeedback('Saved. New research uses these presets; paused work can adopt stricter limits only.')
      window.dispatchEvent(new Event('phlox:research-settings-changed'))
    } catch (error) {
      setFeedback(String(error).replace('Error: ', ''))
    } finally { setBusy(false) }
  }
  if (!presets) return null
  return <form onSubmit={save} className="rounded-lg border border-border bg-surface p-4" aria-label="Research allowances">
    <h4 className="mb-1 flex items-center gap-1.5 text-sm font-semibold text-content"><FlaskConical size={15} className="text-accent" />Research allowances</h4>
    <p className="mb-3 text-xs text-muted">Each preset includes planning and a reserved report pass. The effective Model round limit can reduce it. Time and reported tokens stop gathering between operations; report writing and bounded continuation may add usage. These are not total spend caps.</p>
    <div className="space-y-3">
      {Object.entries(depths).map(([depth, label]) => <fieldset key={depth} disabled={busy} className="rounded-lg border border-border p-3">
        <legend className="px-1 text-sm font-medium text-content">{label}</legend>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          {fields.map(([key, name, min, max]) => <label key={key} className="text-xs text-muted">{name}
            <input aria-label={`${label} ${name}`} type="number" required min={min} max={max} step="1"
              value={presets[depth][key]} onChange={e => setPresets(previous => ({ ...previous, [depth]: { ...previous[depth], [key]: e.target.value } }))}
              className="mt-1 w-full rounded-lg border-border bg-surface-2 text-sm text-content focus:border-accent focus:ring-accent" />
          </label>)}
        </div>
      </fieldset>)}
    </div>
    <p className="my-3 text-xs text-muted">Per-call output/context settings, provider capacity, source storage limits, monthly spend policy and Stop still apply. Larger allowances can increase model and search costs. Research remains opt-in and uses read tools only.</p>
    <button disabled={busy} className="flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-sm text-accent-fg hover:opacity-90 disabled:opacity-50">
      {busy ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}Save research allowances
    </button>
    {feedback && <p role="status" className="mt-2 text-xs text-content">{feedback}</p>}
  </form>
}
