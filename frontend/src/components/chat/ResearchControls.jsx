const input = 'rounded-lg border-border bg-surface-2 py-1 text-xs text-content'
const depths = { brief: 'Up to 3 searches, 4 page reads, 2 minutes of gathering.', standard: 'Up to 6 searches, 8 page reads, 5 minutes of gathering.', thorough: 'Up to 10 searches, 16 page reads, 10 minutes of gathering.' }

export default function ResearchControls({ value, onChange, documents, selected, onSelect, webAllowed, docsAllowed, runsEnabled }) {
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
    <p className="mt-2 text-muted">{depths[value.depth]} Report writing follows. Stop ends further calls.</p>
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
