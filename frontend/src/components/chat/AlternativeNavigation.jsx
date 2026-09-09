import { ChevronLeft, ChevronRight } from 'lucide-react'
import { useStore } from '../../store/useStore'

export default function AlternativeNavigation({ message }) {
  const select = useStore(s => s.selectAlternative)
  const disabled = useStore(s => s.streaming || s.branchSwitching || s.run?.needs_acknowledgement || !!s.live?.pendingApproval)
  const alternatives = message.alternatives || []
  const index = alternatives.indexOf(message.id)
  if (alternatives.length < 2 || index < 0) return null
  const kind = message.role === 'user' ? 'prompt' : 'answer'
  return <nav aria-label={`${kind} alternatives`} className="flex items-center gap-1 text-xs text-muted">
    <button type="button" aria-label={`Previous ${kind} alternative`} disabled={disabled || index === 0}
      onClick={() => select(alternatives[index - 1])} className="rounded p-1.5 hover:bg-surface-2 hover:text-accent disabled:opacity-30"><ChevronLeft size={16} /></button>
    <span aria-live="polite">{index + 1} of {alternatives.length}</span>
    <button type="button" aria-label={`Next ${kind} alternative`} disabled={disabled || index === alternatives.length - 1}
      onClick={() => select(alternatives[index + 1])} className="rounded p-1.5 hover:bg-surface-2 hover:text-accent disabled:opacity-30"><ChevronRight size={16} /></button>
  </nav>
}

export function AlternativeNotice() {
  const visible = useStore(s => s.hasAlternatives)
  return visible ? <p className="text-xs text-muted">Only the selected conversation path is used for replies and exports. Workspace files are shared; switching alternatives does not undo tool actions.</p> : null
}
