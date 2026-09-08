import { ShieldAlert, Check, X } from 'lucide-react'
import { useStore } from '../../store/useStore'

// Shown when the agent paused to ask permission for one or more `ask`-policy tools.
export default function ApprovalPrompt({ pending }) {
  const resolve = useStore((s) => s.resolveApproval)
  const dismiss = useStore((s) => s.dismissApproval)
  const refresh = useStore((s) => s.refreshApproval)
  const streaming = useStore((s) => s.streaming)
  const calls = pending.calls || []
  const status = pending.status || 'pending'
  const resumable = status === 'pending'
  const notices = {
    claimed: 'Execution has started, but its outcome is not confirmed here. It may still be running. Refresh to check; do not repeat the action without checking its result.',
    interrupted: 'Execution was interrupted. Some tools may have run. Check their results before dismissing this notice or starting another turn.',
    failed: 'Execution failed. Some tools may have run. Check their results before dismissing this notice or starting another turn.',
    expired: 'This approval expired. Dismiss it and start a new turn to request fresh actions.',
    legacy: 'This approval predates resumable execution counters. Dismiss it and start a new turn.',
  }

  const decide = (verdict) => {
    const decisions = {}
    for (const c of calls) decisions[c.id] = verdict
    resolve(decisions)
  }

  return (
    <div className="my-2 rounded-xl border border-hutch-gold/60 bg-hutch-gold/10 p-3">
      <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-content">
        <ShieldAlert size={16} className="text-hutch-gold" />
        {resumable ? 'Approval needed' : 'Approval status'}
      </div>
      <p className="mb-3 text-xs text-muted">
        {resumable ? 'Review these tool calls and approve or deny them to continue.' : notices[status]}
        {resumable && pending.expiresAt && ` Expires ${new Date(pending.expiresAt).toLocaleString()}.`}
      </p>
      <div className="mb-3 space-y-1.5">
        {calls.map((c) => (
          <div key={c.id} className="rounded-lg border border-border bg-surface px-3 py-2">
            <div className="font-mono text-xs font-semibold text-content">{c.name}</div>
            <pre className="mt-1 overflow-x-auto text-[11px] text-muted">
              {JSON.stringify(c.arguments, null, 2)}
            </pre>
          </div>
        ))}
      </div>
      <div className="flex flex-wrap gap-2">
        {resumable && <>
        <button
          disabled={streaming}
          onClick={() => decide('allow')}
          className="flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-sm text-accent-fg hover:opacity-90"
        >
          <Check size={15} /> Approve &amp; run
        </button>
        <button
          disabled={streaming}
          onClick={() => decide('deny')}
          className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-content hover:border-red-400 hover:text-red-600"
        >
          <X size={15} /> Deny
        </button>
        </>}
        {status !== 'claimed' && <button onClick={dismiss} disabled={streaming}
          className="rounded-lg border border-border px-3 py-1.5 text-sm text-content hover:bg-surface">
          Dismiss
        </button>}
        <button onClick={() => refresh()} disabled={streaming}
          className="rounded-lg border border-border px-3 py-1.5 text-sm text-content hover:bg-surface">
          Refresh status
        </button>
      </div>
    </div>
  )
}
