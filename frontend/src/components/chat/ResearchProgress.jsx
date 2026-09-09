import { useEffect, useState } from 'react'

const labels = { plan: 'Planning research', gather: 'Gathering evidence', synthesize: 'Writing report', completed: 'Research complete', cancelled: 'Research stopped', failed: 'Research incomplete', paused: 'Research awaiting approval', blocked: 'Research blocked', limit_reached: 'Research limit reached' }
export default function ResearchProgress({ research }) {
  const [, tick] = useState(0)
  const active = ['plan', 'gather', 'synthesize'].includes(research?.phase)
  useEffect(() => {
    if (!active) return undefined
    const timer = setInterval(() => tick(v => v + 1), 1000)
    return () => clearInterval(timer)
  }, [active])
  if (!research) return null
  const elapsed = Math.max(0, Math.round((research.finished_at || Date.now() / 1000) - research.started_at))
  const usage = research.usage || {}
  return <section className="mb-2 rounded-lg border border-border bg-surface-2 p-3 text-xs text-content" aria-label="Research progress">
    <div role="status" className="font-semibold">{labels[research.phase] || 'Research'}</div>
    <p className="mt-1 text-muted">{research.searches}/{research.limits.searches} searches · {research.reads}/{research.limits.reads} page reads · {research.source_count} source records · {Math.floor(elapsed / 60)}m {elapsed % 60}s</p>
    {usage.total > 0 && <p className="mt-1 text-muted">{usage.total.toLocaleString()} reported tokens{usage.unknown_usage_calls ? ' (partial)' : ''} · {usage.cost != null ? `$${usage.cost.toFixed(4)} model cost` : 'model cost unavailable or partial'}. Search API charges are separate.</p>}
    {research.reason && <p className="mt-2">{research.reason}</p>}
    {research.plan && <details className="mt-2"><summary className="cursor-pointer text-accent">Research plan</summary><p className="mt-1 whitespace-pre-wrap">{research.plan}</p></details>}
  </section>
}
