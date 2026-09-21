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
  return <section className="mb-2 break-words rounded-lg border border-border bg-surface-2 p-3 text-xs text-content" aria-label="Research progress">
    <div role="status" className="font-semibold">{labels[research.phase] || 'Research'}</div>
    <p className="mt-1 text-muted">{research.searches}/{research.limits.searches} searches · {research.reads}/{research.limits.reads} source reads · {research.source_count} source records · {Math.floor(elapsed / 60)}m {elapsed % 60}s</p>
    {research.effective_rounds != null && <p className="mt-1 text-muted">{research.rounds_used} model passes used · {research.effective_rounds} planned including report{research.model_round_limit != null ? ` · ${research.model_round_limit} total pass ceiling including continuation` : ''}{research.recovery_calls > 0 ? ` · ${research.recovery_calls} continuation calls` : ''}</p>}
    {research.analysis_enabled && <p className="mt-1 text-muted">Analysis enabled. Execution permissions still apply; remaining Model rounds can finish files after evidence gathering ends.</p>}
    {research.delivery && (research.delivery.available_files.length > 0 || research.delivery.missing_files.length > 0) && <details className="mt-2" aria-label="Research deliverables">
      <summary className="cursor-pointer text-accent">{research.delivery.status === 'partial' ? 'Partially delivered' : 'File delivery'} · {research.delivery.available_files.length} available · {research.delivery.missing_files.length} missing</summary>
      <p className="mt-1 text-muted">Existence checks do not verify contents or chart correctness. Open the saved files below to inspect them.</p>
      {research.delivery.available_files.length > 0 && <p className="mt-1">Available: {research.delivery.available_files.join(', ')}</p>}
      {research.delivery.missing_files.length > 0 && <p className="mt-1">Missing or empty: {research.delivery.missing_files.join(', ')}</p>}
    </details>}
    {research.limits.tokens != null && <p className="mt-1 text-muted">Gathering thresholds: {research.limits.tokens.toLocaleString()} reported tokens · {research.limits.seconds / 60} minutes. Report writing may add usage.{research.source_capacity != null ? ` ${research.source_capacity} new source records available.` : ''}</p>}
    {research.limits_restricted && <p className="mt-1 text-muted">Stricter administrator limits applied when this research resumed.</p>}
    {usage.total > 0 && <p className="mt-1 text-muted">{usage.total.toLocaleString()} reported tokens{usage.unknown_usage_calls ? ' (partial)' : ''} · {usage.cost != null ? `$${usage.cost.toFixed(4)} model cost` : 'model cost unavailable or partial'}. Search API charges are separate.</p>}
    {usage.unknown_usage_calls > 0 && <p className="mt-1 text-muted">Some model usage is unreported. The token threshold cannot measure all usage; missing counts are not zero.</p>}
    {research.reason && <p className="mt-2">{research.reason}</p>}
    {research.plan && <details className="mt-2"><summary className="cursor-pointer text-accent">Research plan</summary><p className="mt-1 whitespace-pre-wrap">{research.plan}</p></details>}
    {research.notebook && <details className="mt-2" aria-label="Research notebook">
      <summary className="cursor-pointer text-accent">Research notebook · revision {research.notebook.revision}</summary>
      <p className="mt-2 text-muted">Working notes derived from sources, not independently verified evidence. Inspect the cited passages before relying on a finding.</p>
      {['findings', 'disagreements'].map(section => research.notebook[section]?.length > 0 && <div key={section} className="mt-2">
        <div className="font-semibold">{section === 'findings' ? 'Findings' : 'Disagreements'}</div>
        <ul className="list-disc space-y-1 pl-4">{research.notebook[section].map((item, index) => <li key={index} className="break-words">
          {item.text} <span className="text-muted">{item.sources.map(label => `[${label}]`).join(' ')}</span>
        </li>)}</ul>
      </div>)}
      {research.notebook.questions?.length > 0 && <div className="mt-2">
        <div className="font-semibold">Open questions</div>
        <ul className="list-disc space-y-1 pl-4">{research.notebook.questions.map((question, index) => <li key={index} className="break-words">{question}</li>)}</ul>
      </div>}
      {research.notebook.unavailable_sources?.length > 0 && <p className="mt-2 text-muted">Notes withheld because their sources are unavailable: {research.notebook.unavailable_sources.join(', ')}.</p>}
      {research.notebook.context && <p className="mt-2 text-muted">
        {research.notebook.context.condensed_exchanges} earlier tool exchanges condensed for the latest model input. The full transcript remains saved.
        {research.notebook.context.restored_sources?.length > 0 && ` Original passages restored: ${research.notebook.context.restored_sources.join(', ')}.`}
        {research.notebook.context.omitted_sources?.length > 0 && ` Full passages omitted from the final evidence packet because of context limits: ${research.notebook.context.omitted_sources.join(', ')}.`}
      </p>}
    </details>}
  </section>
}
