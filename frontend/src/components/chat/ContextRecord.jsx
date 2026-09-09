import { useEffect, useState } from 'react'
import { api } from '../../api/client'

export default function ContextRecord({ conversationId, turnId }) {
  const [open, setOpen] = useState(false)
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  useEffect(() => {
    if (!open) return
    let current = true
    setData(null); setError('')
    api.getContext(conversationId, turnId).then(result => { if (current) setData(result) })
      .catch(() => { if (current) setError('Context record unavailable. Older turns may not have a saved record.') })
    return () => { current = false }
  }, [open, conversationId, turnId])
  return <div className="mt-2 text-xs text-muted">
    <button onClick={() => setOpen(!open)} aria-expanded={open} className="hover:text-accent">{open ? 'Hide context record' : 'Context record'}</button>
    {open && <section aria-label="Context record" className="mt-2 space-y-3 rounded-xl border border-border bg-surface-2 p-3 text-content">
      {error || (!data && 'Loading context record…')}
      {data && <>
        <p><b>{data.project_name || 'Ordinary chat'}</b> · {data.profile} · {data.model}</p>
        <p className="text-muted">This records attempted provider calls and complete retained passages found in their fitted input. It does not prove the provider processed them. Shortened or omitted passages are not listed as fully supplied. Guardrails and context limits still apply.</p>
        <p>{data.calls.length} recorded calls · {data.history_messages} prior messages eligible{data.calls_truncated ? ' · Call record limit reached' : ''}</p>
        <details><summary className="cursor-pointer">Prepared instructions</summary><p className="mt-1 whitespace-pre-wrap break-words">{data.base_instructions}</p>{data.instructions && <p className="mt-2 whitespace-pre-wrap break-words">Project: {data.instructions}</p>}</details>
        <details><summary className="cursor-pointer">Model calls</summary>{data.calls.map((c, i) => <p key={i}>{i + 1}. {c.profile || data.profile} · {c.model} · {c.kind} · {c.source_ids.length} complete passages{c.project_instructions_present ? ' · project instructions present' : ''}</p>)}</details>
        <div><b>Memory</b>{!data.memories.length && <p className="text-muted">No personal memories prepared.</p>}
          {data.memories.map(m => <p key={m.id} className="my-1 break-words">{m.available ? m.content : 'Memory removed.'} <span className="text-muted">{m.supplied ? 'Present in outbound input' : 'Not confirmed in outbound input'}</span></p>)}</div>
        <div><b>Passages supplied to a model call</b>{!data.sources.length && <p className="text-muted">No complete retained source passages confirmed in outbound input.</p>}
          {data.sources.map(s => <details key={s.id} className="mt-2"><summary className="cursor-pointer">[{s.label}] {s.title || 'Unavailable source'}</summary>
            {s.available ? <><p className="my-1 whitespace-pre-wrap break-words">{s.excerpt}</p>{s.url && <a className="break-all text-accent underline" href={s.url} target="_blank" rel="noreferrer">{s.url}</a>}</> : <p>{s.reason}</p>}
          </details>)}</div>
      </>}
    </section>}
  </div>
}
