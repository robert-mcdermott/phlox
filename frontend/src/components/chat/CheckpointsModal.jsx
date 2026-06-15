import { useEffect, useState } from 'react'
import { X, History, RotateCcw, Loader2 } from 'lucide-react'
import { api } from '../../api/client'
import { useStore } from '../../store/useStore'

export default function CheckpointsModal({ conversationId, onClose }) {
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(null)
  const selectConversation = useStore((s) => s.selectConversation)

  const load = () => {
    setLoading(true)
    api
      .listCheckpoints(conversationId)
      .then((r) => setItems(r.checkpoints || []))
      .catch(() => setItems([]))
      .finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [conversationId])

  const restore = async (sha) => {
    setBusy(sha)
    try {
      await api.restoreCheckpoint(conversationId, sha)
      await load()
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div className="max-h-[80vh] w-full max-w-lg overflow-hidden rounded-2xl bg-bg shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <div className="flex items-center gap-2 text-content">
            <History size={18} className="text-accent" />
            <h2 className="font-semibold">Workspace checkpoints</h2>
          </div>
          <button onClick={onClose} className="rounded p-2 text-muted hover:bg-surface-3 hover:text-content">
            <X size={18} />
          </button>
        </div>
        <div className="max-h-[60vh] overflow-y-auto p-4">
          <p className="mb-3 text-xs text-muted">
            Snapshots are taken automatically before the agent changes files. Restore rolls
            the workspace back (your current state is snapshotted first, so nothing is lost).
          </p>
          {loading ? (
            <div className="flex justify-center py-6"><Loader2 className="animate-spin text-accent" /></div>
          ) : items.length === 0 ? (
            <p className="text-sm text-muted">No checkpoints yet for this conversation.</p>
          ) : (
            <div className="space-y-2">
              {items.map((c) => (
                <div key={c.sha} className="flex items-center gap-3 rounded-lg border border-border bg-surface px-3 py-2">
                  <code className="rounded bg-surface-3 px-1.5 py-0.5 text-xs text-content">{c.sha}</code>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm text-content">{c.label}</div>
                    <div className="text-[11px] text-muted">{new Date(c.date).toLocaleString()}</div>
                  </div>
                  <button
                    onClick={() => restore(c.sha)}
                    disabled={busy === c.sha}
                    className="flex items-center gap-1 rounded-lg border border-border px-2 py-1 text-xs text-content hover:border-accent"
                  >
                    {busy === c.sha ? <Loader2 size={13} className="animate-spin" /> : <RotateCcw size={13} />}
                    Restore
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
