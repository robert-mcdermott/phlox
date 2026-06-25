import { useEffect, useState } from 'react'
import { Plus, Trash2, Plug, PlugZap, Loader2 } from 'lucide-react'
import { api } from '../../api/client'

const BLANK = {
  name: '',
  transport: 'stdio',
  command: '',
  args: '',
  url: '',
  authToken: '',
  headers: '',
}

function parseHeaders(value) {
  const text = value.trim()
  if (!text) return null
  if (text.startsWith('{')) return JSON.parse(text)
  return Object.fromEntries(
    text.split('\n')
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => {
        const idx = line.indexOf(':')
        if (idx <= 0) throw new Error(`Invalid header: ${line}`)
        return [line.slice(0, idx).trim(), line.slice(idx + 1).trim()]
      }),
  )
}

export default function McpManager() {
  const [servers, setServers] = useState([])
  const [form, setForm] = useState(BLANK)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)

  const load = () => api.listMcp().then(setServers).catch(() => setServers([]))
  useEffect(() => { load() }, [])

  const add = async () => {
    setBusy(true)
    setErr(null)
    try {
      const body = {
        name: form.name,
        transport: form.transport,
        command: form.transport === 'stdio' ? form.command : null,
        args: form.transport === 'stdio' && form.args ? form.args.split(' ').filter(Boolean) : [],
        url: form.transport !== 'stdio' ? form.url : null,
        auth_token: form.transport !== 'stdio' && form.authToken ? form.authToken : null,
        headers: form.transport !== 'stdio' ? parseHeaders(form.headers) : null,
      }
      await api.addMcp(body)
      setForm(BLANK)
      await load()
    } catch (e) {
      setErr(String(e))
    } finally {
      setBusy(false)
    }
  }

  const toggle = async (s) => {
    setBusy(true)
    try {
      s.connected ? await api.disconnectMcp(s.id) : await api.connectMcp(s.id)
      await load()
    } catch (e) {
      setErr(String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <h3 className="mb-1 text-sm font-semibold text-content">MCP servers</h3>
      <p className="mb-4 text-xs text-muted">
        Connect Model Context Protocol servers. Their tools become available to the
        assistant as <code className="rounded bg-surface-3 px-1">mcp__server__tool</code>.
      </p>

      <div className="mb-5 space-y-2">
        {servers.length === 0 && <p className="text-sm text-muted">No MCP servers configured.</p>}
        {servers.map((s) => (
          <div key={s.id} className="rounded-lg border border-border bg-surface px-3 py-2">
            <div className="flex items-center gap-2">
              <span className={`h-2 w-2 rounded-full ${s.connected ? 'bg-green-500' : 'bg-gray-400'}`} />
              <span className="text-sm font-medium text-content">{s.name}</span>
              <span className="text-xs text-muted">{s.transport}</span>
              <div className="ml-auto flex gap-1">
                <button onClick={() => toggle(s)} disabled={busy}
                  className="rounded p-1.5 text-muted hover:text-accent" title={s.connected ? 'Disconnect' : 'Connect'}>
                  {s.connected ? <PlugZap size={15} className="text-green-600" /> : <Plug size={15} />}
                </button>
                <button onClick={() => api.deleteMcp(s.id).then(load)}
                  className="rounded p-1.5 text-muted hover:text-red-600" title="Delete">
                  <Trash2 size={15} />
                </button>
              </div>
            </div>
            <div className="mt-1 truncate pl-4 text-xs text-muted">
              {s.transport === 'stdio' ? `${s.command} ${(s.args || []).join(' ')}` : s.url}
            </div>
            {s.tools?.length > 0 && (
              <div className="mt-1 flex flex-wrap gap-1 pl-4">
                {s.tools.map((t) => (
                  <span key={t} className="rounded bg-surface-3 px-1.5 py-0.5 text-[11px] text-content">{t.split('__').pop()}</span>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>

      <div className="rounded-lg border border-border bg-surface-2 p-3">
        <div className="mb-2 text-sm font-medium text-content">Add a server</div>
        {err && <div className="mb-2 rounded bg-red-50 px-2 py-1 text-xs text-red-700">{err}</div>}
        <div className="space-y-2">
          <input placeholder="Name (e.g. filesystem)" value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            className="w-full rounded-lg border-border bg-surface text-sm text-content focus:border-accent focus:ring-accent" />
          <select value={form.transport} onChange={(e) => setForm({ ...form, transport: e.target.value })}
            className="w-full rounded-lg border-border bg-surface text-sm text-content focus:border-accent focus:ring-accent">
            <option value="stdio">stdio (command)</option>
            <option value="sse">SSE (URL)</option>
            <option value="http">Streamable HTTP (URL)</option>
          </select>
          {form.transport === 'stdio' ? (
            <>
              <input placeholder="Command (e.g. npx)" value={form.command}
                onChange={(e) => setForm({ ...form, command: e.target.value })}
                className="w-full rounded-lg border-border bg-surface text-sm text-content focus:border-accent focus:ring-accent" />
              <input placeholder="Args (space-separated)" value={form.args}
                onChange={(e) => setForm({ ...form, args: e.target.value })}
                className="w-full rounded-lg border-border bg-surface text-sm text-content focus:border-accent focus:ring-accent" />
            </>
          ) : (
            <>
              <input placeholder={form.transport === 'sse' ? 'https://server/sse' : 'https://server/mcp'} value={form.url}
                onChange={(e) => setForm({ ...form, url: e.target.value })}
                className="w-full rounded-lg border-border bg-surface text-sm text-content focus:border-accent focus:ring-accent" />
              <input placeholder="Bearer token (optional)" value={form.authToken}
                onChange={(e) => setForm({ ...form, authToken: e.target.value })}
                className="w-full rounded-lg border-border bg-surface text-sm text-content focus:border-accent focus:ring-accent" />
              <textarea placeholder={'Headers (optional):\nX-API-Key: value'}
                value={form.headers}
                onChange={(e) => setForm({ ...form, headers: e.target.value })}
                rows={3}
                className="w-full rounded-lg border-border bg-surface text-sm text-content focus:border-accent focus:ring-accent" />
            </>
          )}
          <button onClick={add} disabled={busy || !form.name}
            className="flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-sm text-accent-fg hover:opacity-90 disabled:opacity-50">
            {busy ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />} Add &amp; connect
          </button>
        </div>
      </div>
    </div>
  )
}
