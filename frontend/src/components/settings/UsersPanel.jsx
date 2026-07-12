import { useEffect, useState } from 'react'
import { UserPlus, Trash2, Shield, User as UserIcon, Loader2, KeyRound, Check, X, Building2 } from 'lucide-react'
import { api } from '../../api/client'
import { useStore } from '../../store/useStore'

export default function UsersPanel() {
  const me = useStore((s) => s.user)
  const [users, setUsers] = useState([])
  const [form, setForm] = useState({ username: '', password: '', role: 'user', department: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [resetId, setResetId] = useState(null) // user whose password is being reset
  const [resetPw, setResetPw] = useState('')
  const [deptId, setDeptId] = useState(null) // user whose department is being edited
  const [deptVal, setDeptVal] = useState('')
  const [notice, setNotice] = useState(null)

  const load = () => api.listUsers().then(setUsers).catch(() => setUsers([]))
  useEffect(() => { load() }, [])

  const submitReset = async (u) => {
    if (!resetPw.trim()) return
    await api.updateUser(u.id, { password: resetPw })
    setResetId(null)
    setResetPw('')
    setNotice(`Password reset for ${u.username}; they must replace it at next sign-in.`)
    setTimeout(() => setNotice(null), 3000)
  }

  const submitDept = async (u) => {
    await api.updateUser(u.id, { department: deptVal.trim() })
    setDeptId(null)
    setDeptVal('')
    await load()
  }

  const add = async () => {
    setBusy(true)
    setError(null)
    try {
      await api.createUser(form)
      setForm({ username: '', password: '', role: 'user', department: '' })
      await load()
    } catch (e) {
      setError(String(e).replace('Error: ', ''))
    } finally {
      setBusy(false)
    }
  }

  const toggleRole = (u) =>
    api.updateUser(u.id, { role: u.role === 'admin' ? 'user' : 'admin' }).then(load)
  const toggleActive = (u) => api.updateUser(u.id, { is_active: !u.is_active }).then(load)

  return (
    <div>
      <h3 className="mb-1 text-sm font-semibold text-content">Users</h3>
      <p className="mb-4 text-xs text-muted">
        Manage accounts: roles, password resets, enable/disable, and delete. Each user's
        chats, documents, and memories are <b>private</b> — admins manage accounts but
        cannot view other users' content. Deleting an account permanently removes all of
        that user's data.
      </p>

      {notice && <div className="mb-3 rounded-lg bg-green-50 px-3 py-1.5 text-xs text-green-700">{notice}</div>}

      <div className="mb-5 space-y-2">
        {users.map((u) => (
          <div key={u.id} className="rounded-lg border border-border bg-surface px-3 py-2">
            <div className="flex items-center gap-3">
              {u.role === 'admin' ? <Shield size={16} className="text-hutch-purple" /> : <UserIcon size={16} className="text-muted" />}
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm text-content">
                  {u.display_name || u.username}
                  {u.id === me?.id && <span className="ml-1 text-[10px] text-muted">(you)</span>}
                </div>
                <div className="text-[11px] text-muted">
                  {u.username} · {u.auth_provider}
                  {u.department ? ` · ${u.department}` : ' · no dept'}
                  {!u.is_active && ' · disabled'}
                  {u.must_change_password && ' · password change required'}
                </div>
              </div>
              <button onClick={() => toggleRole(u)} className="rounded border border-border px-2 py-0.5 text-[11px] text-content hover:border-accent">
                {u.role}
              </button>
              <button
                onClick={() => { setDeptId(deptId === u.id ? null : u.id); setDeptVal(u.department || '') }}
                className="rounded p-1 text-muted hover:text-accent"
                title="Set department (for chargeback)"
              >
                <Building2 size={14} />
              </button>
              {u.auth_provider === 'local' && (
                <button
                  onClick={() => { setResetId(resetId === u.id ? null : u.id); setResetPw('') }}
                  className="rounded p-1 text-muted hover:text-accent"
                  title="Reset password"
                >
                  <KeyRound size={14} />
                </button>
              )}
              <button onClick={() => toggleActive(u)} className="text-[11px] text-muted hover:text-accent">
                {u.is_active ? 'disable' : 'enable'}
              </button>
              {u.id !== me?.id && (
                <button
                  onClick={() => {
                    if (confirm(`Delete ${u.username}'s account and ALL their chats, documents, and memories? This cannot be undone.`))
                      api.deleteUser(u.id).then(load)
                  }}
                  className="rounded p-1 text-muted hover:text-red-600"
                  title="Delete account + all data"
                >
                  <Trash2 size={14} />
                </button>
              )}
            </div>
            {resetId === u.id && (
              <div className="mt-2 flex items-center gap-2 border-t border-border pt-2">
                <input
                  type="password"
                  autoFocus
                  value={resetPw}
                  onChange={(e) => setResetPw(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && submitReset(u)}
                  placeholder={`New password for ${u.username}`}
                  className="flex-1 rounded-lg border-border bg-surface-2 text-sm text-content focus:border-accent focus:ring-accent"
                />
                <button onClick={() => submitReset(u)} disabled={!resetPw.trim()}
                  className="rounded-lg bg-accent p-1.5 text-accent-fg hover:opacity-90 disabled:opacity-40" title="Save">
                  <Check size={15} />
                </button>
                <button onClick={() => { setResetId(null); setResetPw('') }} className="rounded-lg p-1.5 text-muted hover:text-content" title="Cancel">
                  <X size={15} />
                </button>
              </div>
            )}
            {deptId === u.id && (
              <div className="mt-2 flex items-center gap-2 border-t border-border pt-2">
                <input
                  autoFocus
                  value={deptVal}
                  onChange={(e) => setDeptVal(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && submitDept(u)}
                  placeholder={`Department for ${u.username} (e.g. cost center)`}
                  className="flex-1 rounded-lg border-border bg-surface-2 text-sm text-content focus:border-accent focus:ring-accent"
                />
                <button onClick={() => submitDept(u)}
                  className="rounded-lg bg-accent p-1.5 text-accent-fg hover:opacity-90" title="Save department">
                  <Check size={15} />
                </button>
                <button onClick={() => { setDeptId(null); setDeptVal('') }} className="rounded-lg p-1.5 text-muted hover:text-content" title="Cancel">
                  <X size={15} />
                </button>
              </div>
            )}
          </div>
        ))}
      </div>

      <div className="rounded-lg border border-border bg-surface-2 p-3">
        <div className="mb-2 text-sm font-medium text-content">Add a user</div>
        {error && <div className="mb-2 rounded bg-red-50 px-2 py-1 text-xs text-red-700">{error}</div>}
        <div className="flex flex-wrap gap-2">
          <input placeholder="username" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })}
            className="flex-1 rounded-lg border-border bg-surface text-sm text-content focus:border-accent focus:ring-accent" />
          <input placeholder="password" type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })}
            className="flex-1 rounded-lg border-border bg-surface text-sm text-content focus:border-accent focus:ring-accent" />
          <input placeholder="department (optional)" value={form.department} onChange={(e) => setForm({ ...form, department: e.target.value })}
            className="flex-1 rounded-lg border-border bg-surface text-sm text-content focus:border-accent focus:ring-accent" />
          <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}
            className="rounded-lg border-border bg-surface text-sm text-content focus:border-accent focus:ring-accent">
            <option value="user">user</option>
            <option value="admin">admin</option>
          </select>
          <button onClick={add} disabled={busy || !form.username || !form.password}
            className="flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-sm text-accent-fg hover:opacity-90 disabled:opacity-50">
            {busy ? <Loader2 size={14} className="animate-spin" /> : <UserPlus size={14} />} Add
          </button>
        </div>
      </div>
    </div>
  )
}
