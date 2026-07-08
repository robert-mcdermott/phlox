import { useState } from 'react'
import { Copy, Check, Brain, Pencil, RefreshCw, FileText, Sparkles } from 'lucide-react'
import Markdown from '../markdown/Markdown'
import ToolCallCard from './ToolCallCard'
import ArtifactViewer from './ArtifactViewer'
import { useStore } from '../../store/useStore'

function CopyBtn({ text }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      onClick={async () => {
        await navigator.clipboard.writeText(text)
        setCopied(true)
        setTimeout(() => setCopied(false), 1500)
      }}
      className="rounded p-1 text-muted opacity-0 transition group-hover:opacity-100 hover:text-content"
      title="Copy"
    >
      {copied ? <Check size={14} className="text-green-500" /> : <Copy size={14} />}
    </button>
  )
}

function Thinking({ text }) {
  const [open, setOpen] = useState(false)
  if (!text) return null
  return (
    <div className="mb-2">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 text-xs text-muted hover:text-content"
      >
        <Brain size={13} /> {open ? 'Hide' : 'Show'} reasoning
      </button>
      {open && (
        <pre className="mt-1 whitespace-pre-wrap rounded bg-surface-2 p-2 text-xs italic text-muted">
          {text}
        </pre>
      )}
    </div>
  )
}

export default function Message({ message, conversationId, isLast }) {
  const editMessage = useStore((s) => s.editMessage)
  const regenerate = useStore((s) => s.regenerate)
  const streaming = useStore((s) => s.streaming)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')

  if (message.role === 'user') {
    const images = (message.attachments || []).filter((a) => a.type === 'image')
    const documents = (message.attachments || []).filter((a) => a.type === 'document')
    const skillRefs = (message.attachments || []).filter((a) => a.type === 'skill')
    const canEdit = !streaming && !String(message.id).startsWith('tmp-')
    if (editing) {
      return (
        <div className="flex justify-end">
          <div className="w-full max-w-[80%]">
            <textarea
              autoFocus
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              rows={Math.min(8, draft.split('\n').length + 1)}
              className="w-full rounded-2xl border-border bg-surface-2 text-sm text-content focus:border-accent focus:ring-accent"
            />
            <div className="mt-1 flex justify-end gap-2">
              <button onClick={() => setEditing(false)} className="rounded-lg px-3 py-1 text-xs text-muted hover:text-content">
                Cancel
              </button>
              <button
                onClick={() => { setEditing(false); editMessage(message.id, draft) }}
                disabled={!draft.trim()}
                className="rounded-lg bg-accent px-3 py-1 text-xs text-accent-fg hover:opacity-90 disabled:opacity-50"
              >
                Save &amp; resend
              </button>
            </div>
          </div>
        </div>
      )
    }
    return (
      <div className="group flex justify-end">
        <div className="flex max-w-[80%] flex-col items-end gap-2">
          {images.length > 0 && (
            <div className="flex flex-wrap justify-end gap-2">
              {images.map((img, i) => (
                <a key={i} href={img.url} target="_blank" rel="noreferrer">
                  <img src={img.url} alt="attachment" className="max-h-48 rounded-xl border border-border object-cover" />
                </a>
              ))}
            </div>
          )}
          {documents.length > 0 && (
            <div className="flex max-w-full flex-wrap justify-end gap-2">
              {documents.map((doc) => (
                <div
                  key={doc.document_id || doc.filename}
                  className="flex max-w-[18rem] items-center gap-1.5 rounded-lg border border-border bg-surface px-2.5 py-1.5 text-xs text-content"
                  title={doc.filename}
                >
                  <FileText size={14} className="shrink-0 text-accent" />
                  <span className="truncate">{doc.filename || 'Document'}</span>
                </div>
              ))}
            </div>
          )}
          {skillRefs.length > 0 && (
            <div className="flex max-w-full flex-wrap justify-end gap-2">
              {skillRefs.map((s) => (
                <div
                  key={s.skill_id || s.name}
                  className="flex max-w-[18rem] items-center gap-1.5 rounded-lg border border-accent/40 bg-accent/10 px-2.5 py-1.5 text-xs text-content"
                  title={`Invoked skill: ${s.name}`}
                >
                  <Sparkles size={14} className="shrink-0 text-accent" />
                  <span className="truncate font-medium">/{s.name}</span>
                </div>
              ))}
            </div>
          )}
          {message.content && (
            <div className="whitespace-pre-wrap rounded-2xl rounded-br-sm bg-user-bubble px-4 py-2.5 text-user-bubble-fg">
              {message.content}
            </div>
          )}
          {canEdit && message.content && (
            <button
              onClick={() => { setDraft(message.content); setEditing(true) }}
              className="flex items-center gap-1 text-[11px] text-muted opacity-0 transition group-hover:opacity-100 hover:text-accent"
            >
              <Pencil size={12} /> Edit
            </button>
          )}
        </div>
      </div>
    )
  }

  const toolCalls = message.tool_calls || message.toolCalls || []
  return (
    <div className="group flex justify-start">
      <div className="w-full max-w-[85%]">
        {message.thinking && <Thinking text={message.thinking} />}
        {toolCalls.map((tc) => (
          <ToolCallCard key={tc.id} call={tc} />
        ))}
        {message.content && (
          <div className="rounded-2xl rounded-bl-sm border border-border bg-surface px-4 py-2 text-content">
            <Markdown>{message.content}</Markdown>
          </div>
        )}
        <ArtifactViewer artifacts={message.artifacts} conversationId={conversationId} />
        {message.content && (
          <div className="mt-1 flex items-center gap-2 pl-1">
            <CopyBtn text={message.content} />
            {isLast && !streaming && !String(message.id).startsWith('tmp-') && (
              <button onClick={regenerate} className="rounded p-1 text-muted hover:text-accent" title="Regenerate">
                <RefreshCw size={14} />
              </button>
            )}
            {message.model && <span className="text-[11px] text-muted">{message.model}</span>}
            {message.usage && (
              <span
                className="text-[11px] text-muted"
                title={`${message.usage.input || 0} in · ${message.usage.output || 0} out` +
                  (message.usage.cost != null ? ` · $${message.usage.cost.toFixed(4)}` : '')}
              >
                · {(message.usage.total || 0).toLocaleString()} tok
                {message.usage.cost != null && message.usage.cost > 0 ? ` · $${message.usage.cost.toFixed(4)}` : ''}
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
