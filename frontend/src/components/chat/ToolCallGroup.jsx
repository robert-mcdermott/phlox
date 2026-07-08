import { useState } from 'react'
import { ChevronDown, ChevronRight, Layers, Loader2 } from 'lucide-react'
import ToolCallCard from './ToolCallCard'

function isRunning(call) {
  return call.running ?? call.content === null
}

// e.g. "web_search ×3, web_fetch ×2" — gives a hint of what happened without expanding.
function summarizeNames(calls) {
  const counts = new Map()
  for (const c of calls) counts.set(c.name, (counts.get(c.name) || 0) + 1)
  return [...counts.entries()].map(([name, n]) => (n > 1 ? `${name} ×${n}` : name)).join(', ')
}

// Renders a run of tool calls for one message. While any call is still running the group
// stays expanded so progress is visible; once everything finishes it auto-collapses into a
// one-line summary (unless the user has manually toggled it, which always wins).
export default function ToolCallGroup({ calls }) {
  const [manualOpen, setManualOpen] = useState(null)

  if (!calls || calls.length === 0) return null

  // A single call doesn't need extra summary chrome around the card.
  if (calls.length === 1) {
    return <ToolCallCard call={calls[0]} />
  }

  const anyRunning = calls.some(isRunning)
  const errorCount = calls.filter((c) => c.is_error).length
  const open = manualOpen === null ? anyRunning : manualOpen

  return (
    <div className="my-2 overflow-hidden rounded-lg border border-border bg-surface-2 text-sm">
      <button
        onClick={() => setManualOpen(!open)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-surface-3"
      >
        {open ? <ChevronDown size={15} className="shrink-0" /> : <ChevronRight size={15} className="shrink-0" />}
        <Layers size={14} className={`shrink-0 ${errorCount > 0 ? 'text-red-500' : 'text-accent'}`} />
        <span className="shrink-0 font-mono text-xs font-semibold text-content">
          {calls.length} tool calls
        </span>
        {!open && (
          <span className="truncate text-xs text-muted">{summarizeNames(calls)}</span>
        )}
        {anyRunning ? (
          <Loader2 size={14} className="ml-auto shrink-0 animate-spin text-accent" />
        ) : (
          <span className="ml-auto shrink-0 text-xs text-muted">
            {errorCount > 0 ? `${errorCount} error${errorCount === 1 ? '' : 's'}` : 'done'}
          </span>
        )}
      </button>
      {open && (
        <div className="border-t border-border px-2 py-1.5">
          {calls.map((tc) => (
            <ToolCallCard key={tc.id} call={tc} />
          ))}
        </div>
      )}
    </div>
  )
}
