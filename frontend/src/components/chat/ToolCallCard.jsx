import { useState } from 'react'
import { ChevronDown, ChevronRight, Terminal, AlertCircle, Loader2 } from 'lucide-react'

const ICONS = {
  execution: '⚙️',
  filesystem: '📁',
  knowledge: '📚',
  web: '🌐',
  mcp: '🔌',
}

export default function ToolCallCard({ call }) {
  const [open, setOpen] = useState(false)
  const running = call.content === null
  const Icon = call.is_error ? AlertCircle : Terminal

  return (
    <div className="my-2 overflow-hidden rounded-lg border border-border bg-surface-2 text-sm">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-surface-3"
      >
        {open ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
        <Icon size={14} className={call.is_error ? 'text-red-500' : 'text-accent'} />
        <span className="font-mono text-xs font-semibold text-content">{call.name}</span>
        {running ? (
          <Loader2 size={14} className="ml-auto animate-spin text-accent" />
        ) : (
          <span className="ml-auto text-xs text-muted">
            {call.is_error ? 'error' : 'done'}
          </span>
        )}
      </button>
      {open && (
        <div className="border-t border-border px-3 py-2 space-y-2">
          <div>
            <div className="mb-1 text-[11px] font-semibold uppercase text-muted">Arguments</div>
            <pre className="overflow-x-auto rounded bg-surface-3 p-2 text-xs text-content">
              {JSON.stringify(call.arguments, null, 2)}
            </pre>
          </div>
          {call.content !== null && (
            <div>
              <div className="mb-1 text-[11px] font-semibold uppercase text-muted">Result</div>
              <pre className="max-h-72 overflow-auto rounded bg-surface-3 p-2 text-xs whitespace-pre-wrap text-content">
                {call.content}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
