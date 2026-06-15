import { Gauge } from 'lucide-react'
import { useStore } from '../../store/useStore'

// Rough token estimate from text (chars / 4) — a display heuristic, not exact.
function estimateTokens(messages) {
  let chars = 0
  for (const m of messages) {
    chars += (m.content || '').length
    for (const tc of m.tool_calls || m.toolCalls || []) {
      chars += JSON.stringify(tc.arguments || {}).length + (tc.content || '').length
    }
  }
  return Math.round(chars / 4)
}

export default function TokenMeter() {
  const messages = useStore((s) => s.messages)
  const live = useStore((s) => s.live)
  const settings = useStore((s) => s.settings)
  const lastUsage = useStore((s) => s.lastUsage)

  if (!settings) return null
  const maxContext = settings.max_context_tokens || 16000
  const maxOut = settings.max_tokens || 8192

  // Context = whole multi-turn conversation that gets sent to the model each turn.
  const ctx = estimateTokens(messages) + (live ? Math.round((live.content || '').length / 4) : 0)
  const pct = Math.min(100, Math.round((ctx / maxContext) * 100))
  const near = pct >= 80
  const barColor = near ? 'var(--color-accent)' : 'var(--color-text-muted)'

  const title =
    `Context (this conversation sent to the model each turn): ~${ctx.toLocaleString()} of ` +
    `${maxContext.toLocaleString()} tokens. Beyond the limit, older turns are summarized.\n` +
    `Per-response output cap: ${maxOut.toLocaleString()} tokens.` +
    (lastUsage ? `\nLast response: ${lastUsage.input} in / ${lastUsage.output} out.` : '')

  return (
    <div className="flex items-center gap-1.5" title={title}>
      <Gauge size={12} className={near ? 'text-hutch-gold' : ''} />
      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-surface-3">
        <div className="h-full rounded-full" style={{ width: `${pct}%`, background: barColor }} />
      </div>
      <span className={near ? 'text-hutch-gold' : ''}>
        {ctx.toLocaleString()}/{(maxContext / 1000).toFixed(0)}k ctx
      </span>
      {lastUsage && (
        <span className="opacity-70">· {lastUsage.output.toLocaleString()}/{(maxOut / 1000).toFixed(0)}k out</span>
      )}
    </div>
  )
}
