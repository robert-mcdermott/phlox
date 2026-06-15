import { useEffect, useState } from 'react'
import { Upload, Trash2, FileText, Loader2, CheckCircle, AlertCircle } from 'lucide-react'
import { api } from '../../api/client'

export default function DocumentsPanel() {
  const [docs, setDocs] = useState([])
  const [uploading, setUploading] = useState(false)

  const load = () => api.listDocuments().then(setDocs).catch(() => setDocs([]))

  useEffect(() => {
    load()
    const t = setInterval(load, 2500) // poll while ingestion runs
    return () => clearInterval(t)
  }, [])

  const upload = async (e) => {
    const file = e.target.files?.[0]
    if (!file) return
    setUploading(true)
    try {
      await api.uploadDocument(file)
      await load()
    } finally {
      setUploading(false)
      e.target.value = ''
    }
  }

  const StatusIcon = ({ s }) =>
    s === 'ready' ? <CheckCircle size={15} className="text-green-600" />
    : s === 'error' ? <AlertCircle size={15} className="text-red-600" />
    : <Loader2 size={15} className="animate-spin text-accent" />

  return (
    <div>
      <h3 className="mb-1 text-sm font-semibold text-content">Knowledge base</h3>
      <p className="mb-4 text-xs text-muted">
        Upload PDFs, Word docs, text, or code. The assistant can search them via the
        <code className="mx-1 rounded bg-surface-3 px-1">search_documents</code> tool.
      </p>

      <label className="mb-4 flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed border-border bg-surface px-4 py-8 text-center hover:border-accent">
        <input type="file" className="hidden" onChange={upload} disabled={uploading} />
        {uploading ? (
          <Loader2 size={22} className="mb-2 animate-spin text-accent" />
        ) : (
          <Upload size={22} className="mb-2 text-accent" />
        )}
        <span className="text-sm text-content">{uploading ? 'Uploading…' : 'Click to upload a document'}</span>
        <span className="text-xs text-muted">PDF · DOCX · TXT · MD · code</span>
      </label>

      <div className="space-y-2">
        {docs.length === 0 && <p className="text-sm text-muted">No documents uploaded yet.</p>}
        {docs.map((d) => (
          <div key={d.id} className="flex items-center gap-3 rounded-lg border border-border bg-surface px-3 py-2">
            <FileText size={18} className="shrink-0 text-accent" />
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm text-content">{d.filename}</div>
              <div className="text-xs text-muted">
                {(d.size_bytes / 1024).toFixed(0)} KB · {d.n_chunks} chunks ·{' '}
                {d.conversation_id ? 'conversation' : 'global'}
                {d.error ? ` · ${d.error}` : ''}
              </div>
            </div>
            <StatusIcon s={d.status} />
            <button
              onClick={() => api.deleteDocument(d.id).then(load)}
              className="rounded p-1.5 text-muted hover:text-red-600"
              title="Delete"
            >
              <Trash2 size={15} />
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}
