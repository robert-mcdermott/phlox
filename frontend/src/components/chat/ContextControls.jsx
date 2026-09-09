import { useEffect, useState } from 'react'
import { Layers } from 'lucide-react'
import { api } from '../../api/client'
import { useStore } from '../../store/useStore'

export default function ContextControls({ value, onChange, research, documentIds, disabled }) {
  const activeId = useStore(s => s.activeId)
  const projectId = useStore(s => s.activeProjectId)
  const assistantId = useStore(s => s.activeAssistantId)
  const settings = useStore(s => s.settings)
  const [open, setOpen] = useState(false)
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const payload = JSON.stringify({ conversation_id: activeId, project_id: projectId,
    assistant_id: assistantId, context: value, research, document_ids: documentIds })
  useEffect(() => {
    if (!open) return
    let current = true
    setBusy(true); setError('')
    api.previewContext(JSON.parse(payload)).then(result => { if (current) setData(result) })
      .catch(() => { if (current) { setData(null); setError('Context preview unavailable. Check the project and provider settings.') } })
      .finally(() => { if (current) setBusy(false) })
    return () => { current = false }
  }, [open, payload, settings?.active_profile, settings?.model])
  useEffect(() => { setOpen(false); setData(null) }, [activeId, projectId])
  const patch = values => onChange({ ...value, ...values })
  const exclude = (key, id, included) => patch({ [key]: included ? (value[key] || []).filter(x => x !== id) : [...(value[key] || []), id] })
  return <div className="mb-2 text-xs text-muted">
    <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className="flex items-center gap-1.5 rounded px-1 py-1 hover:text-accent"><Layers size={14} /> Context{projectId ? ' · Project' : ''}</button>
    {open && <section aria-label="Context preview" className="mt-1 max-h-72 space-y-3 overflow-y-auto rounded-xl border border-border bg-surface p-3 text-content">
      <p>Review the context for this turn. Exclusions do not erase the visible transcript or previous provider requests.</p>
      {busy && <p role="status">Updating preview…</p>}
      {error && <p role="alert">{error}</p>}
      {data && <>
        <p><b>Destination:</b> {data.profile} · {data.model}<br /><span className="text-muted">{data.destination}. Configured provider fallbacks and embedding services may also receive inputs.</span></p>
        <details><summary className="cursor-pointer">Base instructions{data.assistant ? ` · ${data.assistant}` : ''}</summary><p className="mt-1 whitespace-pre-wrap break-words">{data.base_instructions}</p></details>
        <fieldset disabled={disabled} className="space-y-2">
          {data.project_id && <><b>{data.project_name}</b><Toggle label="Use project instructions" checked={value.project_instructions !== false} onChange={v => patch({ project_instructions: v })} />
            {data.instructions && <details><summary className="cursor-pointer">Review project instructions</summary><p className="whitespace-pre-wrap">{data.instructions}</p></details>}</>}
          <Toggle label="Include previous context-compatible turns" checked={value.history !== false} onChange={v => patch({ history: v })} />
          <p className="text-muted">{data.history_messages} previous messages eligible. Changing project context or exclusions starts a new context segment; earlier messages remain visible.</p>
          <Toggle label="Use personal memory" checked={!research && (value.memory ?? !data.project_id)} disabled={!!research} onChange={v => patch({ memory: v })} />
          {research && <p>Research does not use personal memory or prior chat turns.</p>}
          {data.memories?.length > 0 && <details><summary className="cursor-pointer">Memory candidates ({data.memories.length})</summary>
            <p className="my-1 text-muted">Relevant memories are selected when you send. Uncheck any to exclude them.</p>
            {data.memories.map(m => <Toggle key={m.id} label={m.content} checked={!(value.excluded_memory_ids || []).includes(m.id)} onChange={v => exclude('excluded_memory_ids', m.id, v)} />)}
          </details>}
          <div><b>Selected documents</b><p className="my-1 text-muted">Ready project documents provide bounded excerpts. Search stays within project sources and explicit attachments; assistant knowledge remains available within its permissions.</p>
            {!data.documents.length && <p className="text-muted">No project documents selected. Ordinary document-search settings still apply outside projects.</p>}
            {data.documents.map(d => <Toggle key={d.id} label={`${d.filename} · ${d.status}`} checked={d.status === 'ready' && research?.scope !== 'web' && !(value.excluded_document_ids || []).includes(d.id)} disabled={d.status !== 'ready' || research?.scope === 'web'} onChange={v => exclude('excluded_document_ids', d.id, v)} />)}
          </div>
        </fieldset>
      </>}
    </section>}
  </div>
}

function Toggle({ label, checked, onChange, disabled }) {
  return <label className="flex items-start gap-2 py-1"><input type="checkbox" checked={checked} disabled={disabled} onChange={e => onChange(e.target.checked)} className="mt-0.5 rounded border-border text-accent focus:ring-accent" /><span className="min-w-0 break-words">{label}</span></label>
}
