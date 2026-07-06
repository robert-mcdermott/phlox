import { useEffect, useState } from 'react'
import { X, FolderOpen, Download, Image as ImageIcon, FileText, Loader2, RefreshCw, Eye } from 'lucide-react'
import { api } from '../../api/client'
import { canvasKind } from '../../utils/canvas'
import { useStore } from '../../store/useStore'

const IMAGE_EXTS = ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg']

function fmtSize(n) {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

export default function WorkspaceFilesModal({ conversationId, onClose }) {
  const [files, setFiles] = useState([])
  const [loading, setLoading] = useState(true)
  const openCanvasArtifact = useStore((s) => s.openCanvasArtifact)

  const load = () => {
    setLoading(true)
    api
      .listWorkspaceFiles(conversationId)
      .then((r) => setFiles(r.files || []))
      .catch(() => setFiles([]))
      .finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [conversationId])

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div className="max-h-[80vh] w-full max-w-xl overflow-hidden rounded-2xl bg-bg shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <div className="flex items-center gap-2 text-content">
            <FolderOpen size={18} className="text-accent" />
            <h2 className="font-semibold">Workspace files</h2>
          </div>
          <div className="flex items-center gap-1">
            <button onClick={load} className="rounded p-2 text-muted hover:bg-surface-3 hover:text-content" title="Refresh">
              <RefreshCw size={15} />
            </button>
            <button onClick={onClose} className="rounded p-2 text-muted hover:bg-surface-3 hover:text-content">
              <X size={18} />
            </button>
          </div>
        </div>
        <div className="max-h-[64vh] overflow-y-auto p-4">
          <p className="mb-3 text-xs text-muted">
            Every file the agent creates or edits in this conversation's sandbox. Click to download.
          </p>
          {loading ? (
            <div className="flex justify-center py-6"><Loader2 className="animate-spin text-accent" /></div>
          ) : files.length === 0 ? (
            <p className="text-sm text-muted">No files in this workspace yet.</p>
          ) : (
            <div className="space-y-1.5">
              {files.map((f) => {
                const url = api.fileUrl(conversationId, f.path)
                const isImg = IMAGE_EXTS.includes((f.ext || '').toLowerCase())
                const viewable = !isImg && canvasKind(f.ext)
                return (
                  <div
                    key={f.path}
                    className="flex items-center gap-3 rounded-lg border border-border bg-surface px-3 py-2 hover:border-accent"
                  >
                    {isImg ? (
                      <img src={url} alt="" className="h-9 w-9 shrink-0 rounded border border-border object-cover" />
                    ) : (
                      <FileText size={18} className="shrink-0 text-accent" />
                    )}
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm text-content">{f.path}</div>
                      <div className="text-[11px] text-muted">{fmtSize(f.size)}</div>
                    </div>
                    {viewable && (
                      <button
                        onClick={() => { openCanvasArtifact(f, conversationId); onClose() }}
                        className="shrink-0 rounded p-1 text-muted hover:text-accent"
                        title="Open in canvas"
                      >
                        <Eye size={15} />
                      </button>
                    )}
                    <a href={url} download={f.name} className="shrink-0 rounded p-1 text-muted hover:text-accent" title="Download">
                      {isImg ? <ImageIcon size={15} /> : <Download size={15} />}
                    </a>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
