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
    <details className="relative" onKeyDown={e => {
      if (e.key === 'Escape') {
        e.currentTarget.open = false
        e.currentTarget.querySelector('summary')?.focus()
      }
    }}>
      <summary title={title} aria-label={`Token usage: approximately ${pct}% context used`}
        className={`flex h-8 cursor-pointer list-none items-center gap-1.5 whitespace-nowrap rounded-lg px-2 hover:bg-surface-2 focus-visible:outline focus-visible:outline-accent [&::-webkit-details-marker]:hidden ${near ? 'text-accent' : 'text-muted'}`}>
        <Gauge size={13} aria-hidden="true" />
        <span>{pct}% context</span>
      </summary>
      <div className="absolute bottom-full right-0 z-20 mb-2 w-72 max-w-[calc(100vw-3rem)] rounded-xl border border-border bg-surface p-3 text-xs text-content shadow-lg">
        <p className="font-medium">Estimated context usage</p>
        <div className="my-2 h-1.5 overflow-hidden rounded-full bg-surface-3">
          <div className="h-full rounded-full" style={{ width: `${pct}%`, background: barColor }} />
        </div>
        <p>~{ctx.toLocaleString()} / {maxContext.toLocaleString()} tokens</p>
        <p className="mt-1 text-muted">Estimated from conversation text. Older turns are summarized when the context limit is reached.</p>
        <p className="mt-2">Output limit: {maxOut.toLocaleString()} tokens per response.</p>
        {lastUsage && <p className="mt-1 text-muted">Last response: {lastUsage.input.toLocaleString()} in / {lastUsage.output.toLocaleString()} out.</p>}
      </div>
    </details>
  )
}
