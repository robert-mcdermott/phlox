import { useEffect, useRef } from 'react'
import Message from '../components/chat/Message'
import Composer from '../components/chat/Composer'
import ApprovalPrompt from '../components/chat/ApprovalPrompt'
import { useStore } from '../store/useStore'

const SUGGESTIONS = [
  'Write a Python script to plot a sine wave and run it',
  'Search my uploaded documents for the key findings',
  'Explain how this codebase is structured',
  'Create a CSV of sample data and summarize it',
]

function Welcome() {
  const send = useStore((s) => s.sendMessage)
  return (
    <div className="flex h-full flex-col items-center justify-center px-4 text-center">
      <img src="/phlox-logo.svg" alt="Phlox" className="mb-6 h-14" />
      <h1 className="mb-2 text-2xl font-semibold text-content">How can I help you today?</h1>
      <p className="mb-8 max-w-md text-muted">
        Chat, run code, search your documents, and use connected tools — powered by your
        choice of model provider.
      </p>
      <div className="grid w-full max-w-2xl grid-cols-1 gap-2 sm:grid-cols-2">
        {SUGGESTIONS.map((s) => (
          <button
            key={s}
            onClick={() => send(s)}
            className="rounded-xl border border-border bg-surface px-4 py-3 text-left text-sm text-content hover:border-accent"
          >
            {s}
          </button>
        ))}
      </div>
    </div>
  )
}

function ThinkingDots() {
  return (
    <div className="flex gap-1.5 px-1 py-2">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="hc-dot h-2 w-2 rounded-full bg-accent"
          style={{ animationDelay: `${i * 0.16}s` }}
        />
      ))}
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

            {streaming && live && !live.content && live.toolCalls.length === 0 && (
              <div className="flex flex-col gap-1">
                {live.status && <span className="px-1 text-sm text-accent">{live.status}</span>}
                <ThinkingDots />
              </div>
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
      <Composer />
    </div>
  )
}
