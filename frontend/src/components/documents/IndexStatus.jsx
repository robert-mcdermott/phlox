import { useEffect, useState } from 'react'
import { api } from '../../api/client'

export default function IndexStatus() {
  const [status, setStatus] = useState(null)
  const [error, setError] = useState(null)
  const [submitting, setSubmitting] = useState(false)
  useEffect(() => {
    let active = true
    const load = async () => {
      try { const value = await api.documentIndexStatus(); if (active) { setStatus(value); setError(null) } }
      catch (e) { if (active) setError(e.message) }
    }
    load()
    const timer = setInterval(load, 2500)
    return () => { active = false; clearInterval(timer) }
  }, [])
  const busy = submitting || ['queued', 'processing'].includes(status?.status)
  return <section className="mb-4 space-y-2 rounded-lg border border-border bg-surface-2 p-3 text-xs text-content" aria-label="Document search index">
    <h4 className="font-semibold">Document search index</h4>
    <p role="status">{status?.notice || `Search mode: ${status?.mode || 'checking'}`}</p>
    {status?.status && <p>Rebuild: {status.status}{status.total > 0 ? ` · ${status.completed || 0}/${status.total} chunks` : ''}</p>}
    {(error || status?.error) && <p role="alert">{error || status.error}</p>}
    <p className="text-muted">Rebuild all document embeddings with the configured model. This may call your provider for every stored passage. The previous index stays in place until success.</p>
    <button disabled={busy} onClick={async () => {
      setSubmitting(true); setError(null)
      try { const value = await api.rebuildDocumentIndex(); setStatus((old) => ({ ...old, ...value })) }
      catch (e) { setError(e.message) } finally { setSubmitting(false) }
    }} className="rounded border border-border px-2 py-1 disabled:opacity-50">{busy ? 'Rebuilding…' : 'Rebuild search index'}</button>
  </section>
}
