import { useEffect, useMemo, useState } from 'react'
import { Wallet, Trash2, Plus, Loader2, Building2, User as UserIcon } from 'lucide-react'
import { api } from '../../api/client'

const fmtCost = (n) =>
  (n || 0).toLocaleString(undefined, {
    style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2,
  })

// Bar colour tracks headroom: green under warn, amber at/over warn, red at/over limit.
function barColor(pct, warnPct) {
  if (pct >= 100) return 'var(--color-danger, #dc2626)'
  if (pct >= (warnPct || 90)) return '#d97706'
  return 'var(--color-accent)'
}

export default function BudgetsPanel() {
  const [budgets, setBudgets] = useState([])
  const [users, setUsers] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [form, setForm] = useState({ scope_type: 'department', scope_value: '', limit_usd: '', warn_pct: 90 })
  const [busy, setBusy] = useState(false)

  const load = () =>
    api.listBudgets()
      .then((r) => setBudgets(r.budgets || []))
      .catch((e) => setError(String(e).replace('Error: ', '')))
      .finally(() => setLoading(false))

  useEffect(() => {
    load()
    api.listUsers().then(setUsers).catch(() => setUsers([]))
  }, [])

  // Known departments (from users) power the department dropdown.
  const departments = useMemo(
    () => [...new Set(users.map((u) => u.department).filter(Boolean))].sort(),
    [users],
  )

  const add = async () => {
    setBusy(true)
    setError(null)
    try {
      await api.createBudget({
        scope_type: form.scope_type,
        scope_value: form.scope_value.trim(),
        limit_usd: parseFloat(form.limit_usd),
        warn_pct: parseInt(form.warn_pct, 10),
      })
      setForm({ scope_type: form.scope_type, scope_value: '', limit_usd: '', warn_pct: 90 })
      await load()
    } catch (e) {
      setError(String(e).replace('Error: ', ''))
    } finally {
      setBusy(false)
    }
  }

  const patch = (b, body) => api.updateBudget(b.id, body).then(load)
  const remove = (b) => {
    if (confirm(`Delete the ${b.scope_type} budget for "${b.scope_label}"?`))
      api.deleteBudget(b.id).then(load)
  }

  const valid =
    form.scope_value.trim() && form.limit_usd !== '' && parseFloat(form.limit_usd) >= 0

  return (
    <div>
      <h3 className="mb-1 flex items-center gap-2 text-sm font-semibold text-content">
        <Wallet size={15} className="text-accent" /> Spend Budgets
      </h3>
      <p className="mb-4 text-xs text-muted">
        Cap monthly spend per <b>user</b> or <b>department</b>. Users see a warning when they
        cross the warn threshold, and <b>priced</b> models are blocked once a budget is
        reached — models without an assigned cost stay available. Spend is summed from the
        usage ledger over the current month and resets automatically. Most-restrictive budget
        wins when both a user and department budget apply.
      </p>

      {error && <div className="mb-3 rounded bg-red-50 px-3 py-1.5 text-xs text-red-700">{error}</div>}

      {loading ? (
        <div className="flex items-center gap-2 text-sm text-muted"><Loader2 size={14} className="animate-spin" /> Loading…</div>
      ) : (
        <div className="mb-5 space-y-2">
          {budgets.length === 0 && (
            <div className="rounded-lg border border-dashed border-border px-3 py-4 text-center text-xs text-muted">
              No budgets yet. Add one below.
            </div>
          )}
          {budgets.map((b) => (
            <div key={b.id} className="rounded-lg border border-border bg-surface px-3 py-2">
              <div className="flex items-center gap-3">
                {b.scope_type === 'department'
                  ? <Building2 size={16} className="text-hutch-purple" />
                  : <UserIcon size={16} className="text-muted" />}
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm text-content">
                    {b.scope_label}
                    <span className="ml-1 text-[10px] text-muted">({b.scope_type})</span>
                    {!b.is_active && <span className="ml-1 text-[10px] text-muted">· paused</span>}
                  </div>
                  <div className="text-[11px] text-muted">
                    {fmtCost(b.spent_usd)} of {fmtCost(b.limit_usd)} this month · {b.pct}%
                    {b.warn_pct ? ` · warn ${b.warn_pct}%` : ''}
                  </div>
                </div>
                <button onClick={() => patch(b, { is_active: !b.is_active })}
                  className="text-[11px] text-muted hover:text-accent">
                  {b.is_active ? 'pause' : 'resume'}
                </button>
                <button onClick={() => remove(b)} className="rounded p-1 text-muted hover:text-red-600" title="Delete budget">
                  <Trash2 size={14} />
                </button>
              </div>
              {/* spend bar */}
              <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-surface-3">
                <div className="h-full rounded-full transition-all"
                  style={{ width: `${Math.min(b.pct, 100)}%`, background: barColor(b.pct, b.warn_pct) }} />
              </div>
              {/* inline editors */}
              <div className="mt-2 flex flex-wrap items-center gap-2 border-t border-border pt-2 text-[11px] text-muted">
                <label className="flex items-center gap-1">
                  Limit $
                  <input type="number" min="0" step="0.5" defaultValue={b.limit_usd}
                    onBlur={(e) => { const v = parseFloat(e.target.value); if (v >= 0 && v !== b.limit_usd) patch(b, { limit_usd: v }) }}
                    className="w-20 rounded border-border bg-surface-2 py-0.5 text-xs text-content focus:border-accent focus:ring-accent" />
                </label>
                <label className="flex items-center gap-1">
                  Warn %
                  <input type="number" min="0" max="100" defaultValue={b.warn_pct}
                    onBlur={(e) => { const v = parseInt(e.target.value, 10); if (v >= 0 && v <= 100 && v !== b.warn_pct) patch(b, { warn_pct: v }) }}
                    className="w-16 rounded border-border bg-surface-2 py-0.5 text-xs text-content focus:border-accent focus:ring-accent" />
                </label>
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="rounded-lg border border-border bg-surface-2 p-3">
        <div className="mb-2 text-sm font-medium text-content">Add a budget</div>
        <div className="flex flex-wrap items-center gap-2">
          <select value={form.scope_type}
            onChange={(e) => setForm({ ...form, scope_type: e.target.value, scope_value: '' })}
            className="rounded-lg border-border bg-surface text-sm text-content focus:border-accent focus:ring-accent">
            <option value="department">Department</option>
            <option value="user">User</option>
          </select>

          {form.scope_type === 'user' ? (
            <select value={form.scope_value} onChange={(e) => setForm({ ...form, scope_value: e.target.value })}
              className="flex-1 rounded-lg border-border bg-surface text-sm text-content focus:border-accent focus:ring-accent">
              <option value="">Select user…</option>
              {users.map((u) => (
                <option key={u.id} value={u.id}>{u.display_name || u.username}</option>
              ))}
            </select>
          ) : (
            <input list="known-departments" placeholder="Department name"
              value={form.scope_value} onChange={(e) => setForm({ ...form, scope_value: e.target.value })}
              className="flex-1 rounded-lg border-border bg-surface text-sm text-content focus:border-accent focus:ring-accent" />
          )}
          <datalist id="known-departments">
            {departments.map((d) => <option key={d} value={d} />)}
          </datalist>

          <label className="flex items-center gap-1 text-xs text-muted">
            $/mo
            <input type="number" min="0" step="0.5" placeholder="5.00" value={form.limit_usd}
              onChange={(e) => setForm({ ...form, limit_usd: e.target.value })}
              className="w-24 rounded-lg border-border bg-surface text-sm text-content focus:border-accent focus:ring-accent" />
          </label>
          <label className="flex items-center gap-1 text-xs text-muted">
            warn %
            <input type="number" min="0" max="100" value={form.warn_pct}
              onChange={(e) => setForm({ ...form, warn_pct: e.target.value })}
              className="w-16 rounded-lg border-border bg-surface text-sm text-content focus:border-accent focus:ring-accent" />
          </label>
          <button onClick={add} disabled={busy || !valid}
            className="flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-sm text-accent-fg hover:opacity-90 disabled:opacity-50">
            {busy ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />} Add
          </button>
        </div>
      </div>
    </div>
  )
}
