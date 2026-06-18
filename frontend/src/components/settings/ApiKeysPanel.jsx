import { useEffect, useState } from 'react'
import { KeyRound, Trash2, Plus, Copy, Check, AlertTriangle } from 'lucide-react'
import { api } from '../../api/client'

// User-level panel: mint, view, and revoke API keys for the OpenAI-compatible gateway
// (POST /v1/chat/completions, GET /v1/models). The full secret is shown exactly once.
export default function ApiKeysPanel() {
  const [keys, setKeys] = useState([])
  const [name, setName] = useState('')
  const [creating, setCreating] = useState(false)
  // The just-created plaintext secret, shown once until dismissed.
  const [revealed, setRevealed] = useState(null)
  const [copied, setCopied] = useState(false)

  const load = () => api.listApiKeys().then(setKeys).catch(() => setKeys([]))
  useEffect(() => { load() }, [])

  const create = async () => {
    setCreating(true)
    try {
      const created = await api.createApiKey({ name: name.trim() || 'API key' })
      setRevealed(created)
      setName('')
      load()
    } finally {
      setCreating(false)
    }
  }

  const copy = async () => {
    if (!revealed) return
    try {
      await navigator.clipboard.writeText(revealed.key)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      // Clipboard may be unavailable (e.g. non-HTTPS); the user can still select the text.
    }
  }

  const fmtDate = (s) => (s ? new Date(s).toLocaleDateString() : '—')

  return (
    <div>
      <h3 className="mb-1 text-sm font-semibold text-content">API keys</h3>
      <p className="mb-4 text-xs text-muted">
        Use Phlox as an OpenAI-compatible gateway. Point any OpenAI SDK at{' '}
        <code className="rounded bg-surface-3 px-1">{`${window.location.origin}/v1`}</code>{' '}
        with one of these keys as the API key. Usage is metered to your account just like
        chat. Keep keys secret — anyone with a key can spend on your behalf.
      </p>

      <div className="mb-4 flex gap-2">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && !creating && create()}
          placeholder="Name this key (e.g. 'CI pipeline', 'laptop')"
          className="flex-1 rounded-lg border-border bg-surface text-sm text-content focus:border-accent focus:ring-accent"
        />
        <button
          onClick={create}
          disabled={creating}
          className="flex items-center gap-1.5 rounded-lg bg-accent px-3 py-2 text-sm text-accent-fg hover:opacity-90 disabled:opacity-50"
        >
          <Plus size={16} /> Create key
        </button>
      </div>

      {revealed && (
        <div className="mb-4 rounded-lg border border-hutch-gold/40 bg-hutch-gold/10 p-3">
          <div className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold text-content">
            <AlertTriangle size={14} className="text-hutch-gold" />
            Copy your key now — it won’t be shown again.
          </div>
          <div className="flex items-center gap-2">
            <code className="min-w-0 flex-1 overflow-x-auto whitespace-nowrap rounded bg-surface px-2 py-1.5 text-xs text-content">
              {revealed.key}
            </code>
            <button
              onClick={copy}
              className="flex items-center gap-1 rounded-lg border border-border bg-surface px-2 py-1.5 text-xs text-content hover:border-accent"
            >
              {copied ? <Check size={14} className="text-green-500" /> : <Copy size={14} />}
              {copied ? 'Copied' : 'Copy'}
            </button>
            <button
              onClick={() => setRevealed(null)}
              className="rounded-lg px-2 py-1.5 text-xs text-muted hover:text-content"
            >
              Dismiss
            </button>
          </div>
        </div>
      )}

      <div className="space-y-2">
        {keys.length === 0 && <p className="text-sm text-muted">No API keys yet.</p>}
        {keys.map((k) => (
          <div
            key={k.id}
            className={`flex items-center gap-3 rounded-lg border border-border bg-surface px-3 py-2 ${
              k.is_active ? '' : 'opacity-60'
            }`}
          >
            <KeyRound size={16} className="shrink-0 text-accent" />
            <div className="min-w-0 flex-1">
              <div className="text-sm text-content">
                {k.name}{' '}
                <code className="ml-1 rounded bg-surface-3 px-1 text-xs text-muted">
                  {k.prefix}…
                </code>
                {!k.is_active && (
                  <span className="ml-2 rounded bg-red-500/15 px-1.5 py-0.5 text-[10px] text-red-500">
                    revoked
                  </span>
                )}
              </div>
              <div className="mt-0.5 text-[11px] text-muted">
                Created {fmtDate(k.created_at)} · Last used{' '}
                {k.last_used_at ? fmtDate(k.last_used_at) : 'never'}
                {k.expires_at ? ` · Expires ${fmtDate(k.expires_at)}` : ''}
              </div>
            </div>
            {k.is_active && (
              <button
                onClick={() => api.revokeApiKey(k.id).then(load)}
                className="rounded p-1.5 text-muted hover:text-red-600"
                title="Revoke"
              >
                <Trash2 size={15} />
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
