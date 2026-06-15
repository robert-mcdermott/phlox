import { useEffect, useMemo, useState } from 'react'
import { Loader2, Download, BarChart3, ChevronRight, Building2 } from 'lucide-react'
import { api } from '../../api/client'

const fmtTok = (n) => (n || 0).toLocaleString()
const fmtCost = (n) =>
  (n || 0).toLocaleString(undefined, {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 4,
  })

// RFC-4180-ish field escaping for the chargeback CSV.
function csvField(v) {
  const s = String(v ?? '')
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
}

function downloadCsv(rows) {
  const header = [
    'month', 'department', 'username', 'email', 'user_id', 'model',
    'input_tokens', 'output_tokens', 'total_tokens', 'cost_usd', 'turns',
  ]
  const lines = [header.join(',')]
  for (const r of rows) {
    lines.push([
      r.month, r.department, r.username, r.email, r.user_id, r.model,
      r.input_tokens, r.output_tokens, r.total_tokens, r.cost_usd, r.turns,
    ].map(csvField).join(','))
  }
  const blob = new Blob([lines.join('\n')], { type: 'text/csv' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `phlox-usage-${new Date().toISOString().slice(0, 10)}.csv`
  a.click()
  URL.revokeObjectURL(url)
}

export default function UsagePanel() {
  const [data, setData] = useState(null) // { rows, totals }
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [month, setMonth] = useState('all')

  useEffect(() => {
    setLoading(true)
    api
      .usageByUser()
      .then((d) => { setData(d); setError(null) })
      .catch((e) => setError(String(e).replace('Error: ', '')))
      .finally(() => setLoading(false))
  }, [])

  const allRows = data?.rows || []
  const months = useMemo(
    () => Array.from(new Set(allRows.map((r) => r.month))).sort().reverse(),
    [allRows],
  )
  const rows = useMemo(
    () => (month === 'all' ? allRows : allRows.filter((r) => r.month === month)),
    [allRows, month],
  )

  // Roll up to (month, department) — the chargeback unit — each containing a per-user
  // breakdown, each user containing per-model detail.
  const groups = useMemo(() => {
    const byDept = new Map()
    for (const r of rows) {
      const dKey = `${r.month}|${r.department}`
      let d = byDept.get(dKey)
      if (!d) {
        d = { month: r.month, department: r.department, cost: 0, total: 0, turns: 0, users: new Map() }
        byDept.set(dKey, d)
      }
      d.cost += r.cost_usd; d.total += r.total_tokens; d.turns += r.turns

      let u = d.users.get(r.user_id)
      if (!u) {
        u = { user_id: r.user_id, username: r.username, email: r.email,
          input: 0, output: 0, total: 0, cost: 0, turns: 0, models: [] }
        d.users.set(r.user_id, u)
      }
      u.input += r.input_tokens; u.output += r.output_tokens; u.total += r.total_tokens
      u.cost += r.cost_usd; u.turns += r.turns
      u.models.push(r)
    }
    const out = [...byDept.values()].map((d) => ({
      ...d,
      users: [...d.users.values()]
        .map((u) => ({ ...u, models: u.models.slice().sort((a, b) => b.cost_usd - a.cost_usd) }))
        .sort((a, b) => b.cost - a.cost),
    }))
    out.sort((a, b) => (a.month === b.month
      ? a.department.localeCompare(b.department)
      : b.month.localeCompare(a.month)))
    return out
  }, [rows])

  // Totals reflect the current month filter.
  const totals = useMemo(
    () => rows.reduce(
      (t, r) => {
        t.input += r.input_tokens; t.output += r.output_tokens
        t.total += r.total_tokens; t.cost += r.cost_usd; t.turns += r.turns
        return t
      },
      { input: 0, output: 0, total: 0, cost: 0, turns: 0 },
    ),
    [rows],
  )

  return (
    <div>
      <div className="mb-1 flex items-center gap-2">
        <BarChart3 size={16} className="text-hutch-purple" />
        <h3 className="text-sm font-semibold text-content">Usage & Cost</h3>
      </div>
      <p className="mb-4 text-xs text-muted">
        Token usage and cost grouped by <b>department</b>, then user and model, bucketed by
        month (UTC), for chargeback accounting. Reads a durable usage ledger (metadata only —
        never message content), so usage of <b>deleted users still appears</b> under the
        department they belonged to. Cost is computed from the per-model <code>pricing</code>{' '}
        (set in <b>Admin → Configuration</b> or seeded from <code>config.yml</code>); models
        without a configured price show tokens but <b>$0</b>.
      </p>

      {loading && (
        <div className="flex items-center gap-2 text-sm text-muted">
          <Loader2 size={15} className="animate-spin" /> Loading usage…
        </div>
      )}
      {error && (
        <div className="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">{error}</div>
      )}

      {!loading && !error && (
        <>
          <div className="mb-4 flex flex-wrap items-center gap-3">
            <select
              value={month}
              onChange={(e) => setMonth(e.target.value)}
              className="rounded-lg border-border bg-surface text-sm text-content focus:border-accent focus:ring-accent"
            >
              <option value="all">All months</option>
              {months.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
            <div className="flex-1" />
            <button
              onClick={() => downloadCsv(rows)}
              disabled={!rows.length}
              className="flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-sm text-accent-fg hover:opacity-90 disabled:opacity-50"
            >
              <Download size={14} /> Export CSV
            </button>
          </div>

          {/* Grand totals for the current filter. */}
          <div className="mb-4 grid grid-cols-3 gap-2 sm:grid-cols-4">
            <Stat label="Cost" value={fmtCost(totals.cost)} accent />
            <Stat label="Total tokens" value={fmtTok(totals.total)} />
            <Stat label="In / Out" value={`${fmtTok(totals.input)} / ${fmtTok(totals.output)}`} />
            <Stat label="Turns" value={fmtTok(totals.turns)} />
          </div>

          {!rows.length ? (
            <div className="rounded-lg border border-border bg-surface px-3 py-6 text-center text-sm text-muted">
              No usage recorded{month === 'all' ? '' : ` for ${month}`}.
            </div>
          ) : (
            <div className="space-y-2">
              {groups.map((d) => (
                <DeptBlock key={`${d.month}|${d.department}`} d={d} />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  )
}

function Stat({ label, value, accent }) {
  return (
    <div className="rounded-lg border border-border bg-surface-2 px-3 py-2">
      <div className="text-[10px] uppercase tracking-wide text-muted">{label}</div>
      <div className={`truncate text-sm font-semibold ${accent ? 'text-accent' : 'text-content'}`} title={value}>
        {value}
      </div>
    </div>
  )
}

// A department's monthly chargeback total, expandable into its per-user breakdown.
function DeptBlock({ d }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="rounded-lg border border-border bg-surface">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-3 px-3 py-2 text-left"
      >
        <ChevronRight size={14} className={`shrink-0 text-muted transition-transform ${open ? 'rotate-90' : ''}`} />
        <Building2 size={15} className="shrink-0 text-hutch-purple" />
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium text-content">{d.department}</div>
          <div className="text-[11px] text-muted">
            {d.month} · {d.users.length} user{d.users.length === 1 ? '' : 's'} · {d.turns} turns
          </div>
        </div>
        <div className="text-right">
          <div className="text-sm font-semibold text-accent">{fmtCost(d.cost)}</div>
          <div className="text-[11px] text-muted">{fmtTok(d.total)} tok</div>
        </div>
      </button>
      {open && (
        <div className="space-y-1.5 border-t border-border px-3 py-2">
          {d.users.map((u) => <UserRow key={u.user_id} u={u} />)}
        </div>
      )}
    </div>
  )
}

// A single user within a department: their cost, expandable into per-model detail.
function UserRow({ u }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="rounded-md bg-surface-2">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-2 py-1.5 text-left"
      >
        <ChevronRight size={12} className={`shrink-0 text-muted transition-transform ${open ? 'rotate-90' : ''}`} />
        <div className="min-w-0 flex-1">
          <div className="truncate text-[13px] text-content">{u.username}</div>
          <div className="text-[10px] text-muted">{u.turns} turns · {u.models.length} model{u.models.length === 1 ? '' : 's'}</div>
        </div>
        <div className="text-right">
          <div className="text-[13px] font-medium text-content">{fmtCost(u.cost)}</div>
          <div className="text-[10px] text-muted">{fmtTok(u.total)} tok</div>
        </div>
      </button>
      {open && (
        <div className="px-2 pb-2">
          <table className="w-full text-[11px]">
            <thead>
              <tr className="text-muted">
                <th className="py-1 text-left font-medium">Model</th>
                <th className="py-1 text-right font-medium">In</th>
                <th className="py-1 text-right font-medium">Out</th>
                <th className="py-1 text-right font-medium">Turns</th>
                <th className="py-1 text-right font-medium">Cost</th>
              </tr>
            </thead>
            <tbody>
              {u.models.map((m) => (
                <tr key={m.model} className="border-t border-border/60 text-content">
                  <td className="py-1 pr-2 font-mono">{m.model}</td>
                  <td className="py-1 text-right">{fmtTok(m.input_tokens)}</td>
                  <td className="py-1 text-right">{fmtTok(m.output_tokens)}</td>
                  <td className="py-1 text-right">{m.turns}</td>
                  <td className="py-1 text-right">{fmtCost(m.cost_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
