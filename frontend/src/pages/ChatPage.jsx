import { useEffect, useRef } from 'react'
import { AlertTriangle, Ban } from 'lucide-react'
import Message from '../components/chat/Message'
import Composer from '../components/chat/Composer'
import ApprovalPrompt from '../components/chat/ApprovalPrompt'
import AssistantAvatar from '../components/assistants/AssistantAvatar'
import { useStore } from '../store/useStore'

const fmtUsd = (n) =>
  (n || 0).toLocaleString(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 2 })

// Banner shown when the signed-in user is over (blocked) or near (warn) a monthly budget.
function BudgetBanner() {
  const budget = useStore((s) => s.budget)
  if (!budget || (!budget.blocked && !budget.warn)) return null
  const w = budget.worst || {}
  const where = w.scope_type === 'department' ? 'Your department' : 'You'
  const blocked = budget.blocked
  return (
    <div
      className={`mx-auto mb-2 flex max-w-3xl items-start gap-2 rounded-lg border px-4 py-2 text-sm ${
        blocked
          ? 'border-red-300 bg-red-50 text-red-700'
          : 'border-amber-300 bg-amber-50 text-amber-800'
      }`}
      role="status"
    >
      {blocked ? <Ban size={16} className="mt-0.5 shrink-0" /> : <AlertTriangle size={16} className="mt-0.5 shrink-0" />}
      <div>
        {blocked ? (
          <>
            <b>Monthly budget reached.</b> {where} {where === 'You' ? 'have' : 'has'} used{' '}
            {fmtUsd(w.spent_usd)} of the {fmtUsd(w.limit_usd)} budget. Models with an assigned
            cost are paused until the budget resets next month; free models still work.
          </>
        ) : (
          <>
            <b>Approaching budget limit.</b> {where} {where === 'You' ? 'have' : 'has'} used{' '}
            {fmtUsd(w.spent_usd)} of {fmtUsd(w.limit_usd)} ({w.pct}%) this month.
          </>
        )}
      </div>
    </div>
  )
}

function Welcome() {
  const send = useStore((s) => s.sendMessage)
  const assistants = useStore((s) => s.assistants)
  const activeAssistantId = useStore((s) => s.activeAssistantId)
  const selectAssistant = useStore((s) => s.selectAssistant)
  // Deployment-wide starter prompts (admin-editable in Settings → Admin → Configuration);
  // null while loading, so the grid renders empty rather than flashing stale defaults.
  const configured = useStore((s) => s.suggestions)
  const assistant = assistants.find((a) => a.id === activeAssistantId) || null

  const suggestions = assistant?.prompt_suggestions?.length
    ? assistant.prompt_suggestions.map((text) => ({ text }))
    : assistant
      ? []
      : (configured || []).map((s) => ({
          text: s.text,
          documentSearch: s.document_search,
          webSearch: s.web_search,
        }))

  return (
    <div className="flex h-full flex-col items-center justify-center px-4 text-center">
      {assistant ? (
        <AssistantAvatar assistant={assistant} size={56} className="mb-6" />
      ) : (
        <img src="/phlox-logo.svg" alt="Phlox" className="mb-6 h-14" />
      )}
      <h1 className="mb-2 text-2xl font-semibold text-content">
        {assistant ? assistant.name : 'How can I help you today?'}
      </h1>
      <p className="mb-8 max-w-md text-muted">
        {assistant
          ? assistant.description || 'Ask me anything in my area of expertise.'
          : 'Chat, run code, search your documents, and use connected tools — powered by your choice of model provider.'}
      </p>

      {assistants.length > 0 && (
        <div className="mb-8 flex w-full max-w-2xl flex-wrap justify-center gap-2">
          <button
            onClick={() => selectAssistant(null)}
            className={`flex items-center gap-2 rounded-full border px-3 py-1.5 text-sm ${
              !assistant
                ? 'border-accent bg-surface-2 text-content'
                : 'border-border bg-surface text-muted hover:border-accent'
            }`}
          >
            <img src="/phlox-logo.svg" alt="" className="h-4" />
            Phlox
          </button>
          {assistants.map((a) => (
            <button
              key={a.id}
              onClick={() => selectAssistant(a.id)}
              title={a.description || a.name}
              className={`flex items-center gap-2 rounded-full border px-3 py-1.5 text-sm ${
                a.id === activeAssistantId
                  ? 'border-accent bg-surface-2 text-content'
                  : 'border-border bg-surface text-muted hover:border-accent'
              }`}
            >
              <AssistantAvatar assistant={a} size={20} />
              {a.name}
            </button>
          ))}
        </div>
      )}

      <div className="grid w-full max-w-2xl grid-cols-1 gap-2 sm:grid-cols-2">
        {suggestions.map((s) => (
          <button
            key={s.text}
            onClick={() =>
              send(s.text, {
                documentSearch: Boolean(s.documentSearch),
                webSearch: Boolean(s.webSearch),
              })
            }
            className="rounded-xl border border-border bg-surface px-4 py-3 text-left text-sm text-content hover:border-accent"
          >
            {s.text}
          </button>
        ))}
      </div>
    </div>
  )
}

// True while at least one tool call is still executing (no result yet).
// Those cards render their own spinner, so the page-level indicator yields to them;
// in the gaps between tool steps (all results in, next step not started) it stays visible.
function anyToolRunning(live) {
  return live.toolCalls.some((tc) => tc.content === null)
}

function ThinkingDots({ label }) {
  return (
    <div className="flex items-center gap-2 px-1 py-2" role="status" aria-live="polite">
      <div className="flex gap-1.5">
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="hc-dot h-2 w-2 rounded-full bg-accent"
            style={{ animationDelay: `${i * 0.16}s` }}
          />
        ))}
      </div>
      <span className="text-sm text-muted">{label || 'Working\u2026'}</span>
    </div>
  )
}

export default function ChatPage() {
  const messages = useStore((s) => s.messages)
  const live = useStore((s) => s.live)
  const streaming = useStore((s) => s.streaming)
  const activeId = useStore((s) => s.activeId)
  const error = useStore((s) => s.error)
  const endRef = useRef(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, live])

  const empty = messages.length === 0 && !live

  return (
    <div className="flex flex-1 flex-col min-h-0">
      <div className="flex-1 overflow-y-auto">
        {empty ? (
          <Welcome />
        ) : (
          <div className="mx-auto max-w-3xl space-y-5 px-4 py-6">
            {messages.map((m, i) => (
              <Message
                key={m.id}
                message={m}
                conversationId={activeId}
                isLast={!live && i === messages.length - 1}
              />
            ))}

            {live && (
              <>
                <Message
                  message={{
                    id: 'live',
                    role: 'assistant',
                    content: live.content,
                    thinking: live.thinking,
                    toolCalls: live.toolCalls,
                    artifacts: live.artifacts,
                  }}
                  conversationId={activeId}
                />
                {live.pendingApproval && (
                  <div className="flex justify-start">
                    <div className="w-full max-w-[85%]">
                      <ApprovalPrompt pending={live.pendingApproval} />
                    </div>
                  </div>
                )}
              </>
            )}

            {streaming && live && !live.pendingApproval && !anyToolRunning(live) && (
              <ThinkingDots label={live.status || (live.content ? 'Responding\u2026' : 'Working\u2026')} />
            )}

            {error && (
              <div className="rounded-lg border border-red-300 bg-red-50 px-4 py-2 text-sm text-red-700">
                {error}
              </div>
            )}
            <div ref={endRef} />
          </div>
        )}
      </div>
      <div className="px-4 pt-2">
        <BudgetBanner />
      </div>
      <Composer />
    </div>
  )
}
