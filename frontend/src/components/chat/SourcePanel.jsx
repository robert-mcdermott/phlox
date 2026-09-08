import { useEffect, useRef, useState } from 'react'
import { X } from 'lucide-react'
import { authHeaders } from '../../api/token'

export default function SourcePanel({ conversationId, reference, onClose }) {
  const dialog = useRef(null)
  const [source, setSource] = useState(null)
  const [error, setError] = useState(null)
  useEffect(() => {
    const element = dialog.current
    const origin = element.parentElement
    element.showModal()
    const controller = new AbortController()
    let active = true
    ;(async () => {
      try {
        const response = await fetch(`/api/conversations/${conversationId}/sources/${reference.source_id}`, {
          headers: authHeaders(), signal: controller.signal,
        })
        if (!response.ok) throw new Error(response.status === 404 ? 'Source unavailable.' : 'Could not load this source. Close and reopen to retry.')
        const result = await response.json()
        if (active) setSource(result)
      } catch (err) {
        if (active && err.name !== 'AbortError') setError(err.message)
      }
    })()
    return () => {
      active = false
      controller.abort()
      element.close()
      // Markdown may have replaced the original button while the answer streamed.
      origin?.querySelector(`[aria-label="View source ${reference.label}"]`)?.focus()
    }
  }, [conversationId, reference.source_id, reference.label])
  return <dialog ref={dialog} onCancel={(event) => { event.preventDefault(); onClose() }}
    onClick={(event) => { if (event.target === dialog.current) onClose() }}
    aria-labelledby="source-panel-title"
    className="w-[min(42rem,92vw)] max-h-[85vh] overflow-y-auto rounded-xl border border-border bg-surface p-0 text-content shadow-xl backdrop:bg-black/50">
    <div className="flex items-center justify-between border-b border-border px-5 py-3">
      <h2 id="source-panel-title" className="font-semibold">Source {reference.label}</h2>
      <button onClick={onClose} aria-label="Close source" className="rounded p-2 text-muted hover:text-content"><X size={18} /></button>
    </div>
    <div className="space-y-3 p-5 text-sm">
      {!source && !error && <p role="status">Loading source…</p>}
      {error && <p role="alert">{error}</p>}
      {source && !source.available && <p role="status">{source.reason || 'Source unavailable.'}</p>}
      {source?.available && <>
        <h3 className="break-words font-semibold">{source.title}</h3>
        {source.location.page && <p className="text-muted">Page {source.location.page}</p>}
        {source.location.section && <p className="text-muted">Section: {source.location.section}</p>}
        {source.location.table_row && <p className="text-muted">Table {source.location.table || ''} row {source.location.table_row}</p>}
        <p className="text-muted">Chunk {source.location.chunk + 1} · Characters {source.location.start + 1}–{source.location.end}</p>
        <p className="text-muted">Captured {new Date(source.captured_at).toLocaleString()}</p>
        {source.changed && <p role="status">The document has changed since this excerpt was captured.</p>}
        {source.location.truncated && <p role="status">Shortened excerpt. Additional chunk text was not retained.</p>}
        <h4 className="font-medium">Captured excerpt</h4>
        <blockquote className="whitespace-pre-wrap break-words rounded-lg border-l-2 border-accent bg-surface-2 p-4">{source.excerpt}</blockquote>
        <p className="text-xs text-muted">This reference identifies a passage supplied to the model. Check whether it supports the answer’s claim.</p>
      </>}
    </div>
  </dialog>
}
