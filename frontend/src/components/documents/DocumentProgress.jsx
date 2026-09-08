import { useState } from 'react'

export default function DocumentProgress({ document: doc, onRetry }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const active = ['pending', 'queued', 'processing'].includes(doc.status)
  const progress = doc.ingestion || {}
  return <div className="space-y-1 text-xs text-muted">
    <p role="status">{progress.stage || doc.status}{active && progress.total > 0 ? ` · ${progress.completed || 0}/${progress.total} chunks` : ''}</p>
    {active && progress.total > 0 && <progress aria-label={`Processing ${doc.filename}`} max={progress.total} value={progress.completed || 0} className="h-1 w-full accent-accent" />}
    {!active && <button disabled={busy} onClick={async () => {
      setBusy(true); setError(null)
      try { await onRetry() } catch (e) { setError(e.message) } finally { setBusy(false) }
    }} className="rounded border border-border px-2 py-1 text-content disabled:opacity-50">
      {busy ? 'Queuing…' : doc.status === 'ready' ? 'Reprocess document' : 'Retry processing'}
    </button>}
    {error && <p role="alert">{error}</p>}
  </div>
}
