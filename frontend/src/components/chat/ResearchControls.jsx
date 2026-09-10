import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import { useStore } from '../../store/useStore'

const input = 'rounded-lg border-border bg-surface-2 py-1 text-xs text-content'

export default function ResearchControls({ value, onChange, documents, selected, onSelect, webAllowed, docsAllowed, runsEnabled }) {
  const [config, setConfig] = useState(null)
  const [error, setError] = useState(false)
  const roundLimit = useStore(s => s.settings?.max_tool_rounds)
  useEffect(() => {
    let active = true, revision = 0
    const load = () => {
      const request = ++revision
      api.getResearchConfig().then(data => {
        if (active && request === revision) { setConfig(data); setError(false) }
      }).catch(() => { if (active && request === revision) { setConfig(null); setError(true) } })
    }
    load()
    window.addEventListener('focus', load)
    window.addEventListener('phlox:research-settings-changed', load)
    return () => {
      active = false
      window.removeEventListener('focus', load)
      window.removeEventListener('phlox:research-settings-changed', load)
    }
  }, [])
  const limits = config?.presets?.[value.depth]
  return <div className="mb-3 rounded-lg border border-border bg-surface-2 p-3 text-xs text-content">
    <div className="flex flex-wrap gap-3">
      <label className="flex items-center gap-2">Sources
        <select aria-label="Research sources" className={input} value={value.scope} onChange={e => onChange({ ...value, scope: e.target.value })}>
          <option value="web" disabled={!webAllowed}>Web</option>
          <option value="documents" disabled={!docsAllowed}>Selected documents</option>
          <option value="both" disabled={!webAllowed || !docsAllowed}>Documents + web</option>
        </select>
      </label>
      <label className="flex items-center gap-2">Depth
        <select aria-label="Research depth" className={input} value={value.depth} onChange={e => onChange({ ...value, depth: e.target.value })}>
          <option value="brief">Brief</option><option value="standard">Standard</option><option value="thorough">Thorough</option>
        </select>
      </label>
    </div>
    {limits ? <div className="mt-2 text-muted" aria-label="Research budget">
      <p>Up to {limits.searches} searches{value.scope !== 'documents' ? `, ${limits.reads} page reads` : ''} · {limits.seconds / 60} minutes · {limits.tokens.toLocaleString()} reported tokens before gathering stops.</p>
      <p>{limits.rounds} planned model passes, including the report.{roundLimit < limits.rounds ? ` Your Model setting is ${roundLimit} passes; raise Max tool rounds to use the full preset.` : ''} Assistant and conversation overrides also apply.</p>
      <p>Report writing and continuation can add time and tokens. Source storage holds up to {config.source_limit} records per turn; a page may use several. Stop ends further calls.</p>
    </div> : <p role="status" className="mt-2 text-muted">{error ? 'Research allowances could not be loaded. The server will apply its current limits.' : 'Loading research allowances…'}</p>}
    {value.scope !== 'web' && <fieldset className="mt-3"><legend className="mb-1 font-medium">Choose documents</legend>
      <div className="max-h-32 overflow-y-auto space-y-1">
        {documents.filter(d => d.status === 'ready').map(doc => <label key={doc.id} className="flex items-center gap-2">
          <input type="checkbox" className="rounded border-border text-accent focus:ring-accent" checked={selected.some(d => (d.id || d.document_id) === doc.id)} onChange={e => onSelect(e.target.checked ? [...selected, doc] : selected.filter(d => (d.id || d.document_id) !== doc.id))} />
          <span className="truncate">{doc.filename}</span>
        </label>)}
        {!documents.some(d => d.status === 'ready') && <p className="text-muted">Attach a document below, or add one in Settings → Documents.</p>}
      </div>
    </fieldset>}
    {value.scope !== 'documents' && <label className="mt-3 block text-muted">Domains (optional, comma separated)
      <input aria-label="Research domains" className={`${input} mt-1 w-full`} value={value.domainsText || ''} placeholder="example.org, docs.example.com" onChange={e => onChange({ ...value, domainsText: e.target.value })} />
    </label>}
    <p className="mt-2 text-muted">Research uses this question and selected sources. Normal chat retains your conversation context. The deep-research skill remains available in chat; selecting a skill does not enable this mode.</p>
    {!runsEnabled && <p className="mt-2 text-muted">Keep this chat open while researching. An administrator can enable reconnectable runs for background continuity.</p>}
  </div>
}
