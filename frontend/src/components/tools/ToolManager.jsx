import { useEffect, useState } from 'react'
import { api } from '../../api/client'

const PERMISSIONS = ['auto', 'ask', 'deny']

export default function ToolManager() {
  const [tools, setTools] = useState([])

  const load = () => api.listTools().then(setTools).catch(() => setTools([]))
  useEffect(() => { load() }, [])

  const update = async (name, patch) => {
    setTools((ts) => ts.map((t) => (t.name === name ? { ...t, ...patch } : t)))
    await api.updateTool(name, patch)
  }

  const groups = tools.reduce((acc, t) => {
    ;(acc[t.category] = acc[t.category] || []).push(t)
    return acc
  }, {})

  return (
    <div>
      <h3 className="mb-1 text-sm font-semibold text-content">Tools &amp; permissions</h3>
      <p className="mb-4 text-xs text-muted">
        Enable/disable tools and set how they run: <b>auto</b> (run freely), <b>ask</b>
        {' '}(needs Agent mode), or <b>deny</b> (never).
      </p>

      {Object.entries(groups).map(([cat, list]) => (
        <div key={cat} className="mb-5">
          <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">{cat}</div>
          <div className="space-y-2">
            {list.map((t) => (
              <div key={t.name} className="flex items-center gap-3 rounded-lg border border-border bg-surface px-3 py-2">
                <input
                  type="checkbox"
                  checked={t.enabled}
                  onChange={(e) => update(t.name, { enabled: e.target.checked })}
                  className="rounded border-border text-accent focus:ring-accent"
                />
                <div className="min-w-0 flex-1">
                  <div className="font-mono text-xs font-semibold text-content">{t.name}</div>
                  <div className="truncate text-xs text-muted">{t.description}</div>
                </div>
                <select
                  value={t.permission}
                  onChange={(e) => update(t.name, { permission: e.target.value })}
                  className="rounded-lg border-border bg-surface text-xs text-content focus:border-accent focus:ring-accent"
                >
                  {PERMISSIONS.map((p) => (
                    <option key={p} value={p}>{p}</option>
                  ))}
                </select>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}
