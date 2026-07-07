import { useEffect, useState } from 'react'
import {
  Bot, Plus, Trash2, Pencil, X, Upload, FileText, Loader2, CheckCircle, AlertCircle,
  ImagePlus,
} from 'lucide-react'
import { api } from '../../api/client'
import { useStore } from '../../store/useStore'
import AssistantAvatar from '../assistants/AssistantAvatar'

const BLANK = {
  name: '',
  description: '',
  avatar: null,
  profile: '',
  model: '',
  system_prompt: '',
  prompt_suggestions: [],
  capabilities: { web_search: true, document_search: true, tools: true },
  visibility: 'public',
}

const CAPABILITIES = [
  { key: 'web_search', label: 'Web search', hint: 'Allow the web-search tool' },
  { key: 'document_search', label: 'Personal documents', hint: "Allow searching the user's own uploaded documents" },
  { key: 'tools', label: 'Agent tools', hint: 'Allow code execution, shell, files, and MCP tools' },
]

// Downscale an image file to a small square data URL so avatars stay tiny.
function fileToAvatarDataUrl(file, size = 128) {
  return new Promise((resolve, reject) => {
    const img = new Image()
    const url = URL.createObjectURL(file)
    img.onload = () => {
      URL.revokeObjectURL(url)
      const canvas = document.createElement('canvas')
      canvas.width = size
      canvas.height = size
      const scale = Math.max(size / img.width, size / img.height)
      const w = img.width * scale
      const h = img.height * scale
      canvas.getContext('2d').drawImage(img, (size - w) / 2, (size - h) / 2, w, h)
      resolve(canvas.toDataURL('image/jpeg', 0.85))
    }
    img.onerror = () => {
      URL.revokeObjectURL(url)
      reject(new Error('Could not read image'))
    }
    img.src = url
  })
}

function Field({ label, hint, children }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-content">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[11px] text-muted">{hint}</span>}
    </label>
  )
}

const inputCls =
  'w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-content outline-none focus:border-accent'

function KnowledgeSection({ assistantId }) {
  const [docs, setDocs] = useState([])
  const [uploading, setUploading] = useState(false)

  const load = () =>
    api.listAssistantDocuments(assistantId).then(setDocs).catch(() => setDocs([]))

  useEffect(() => {
    if (!assistantId) return undefined
    load()
    const t = setInterval(load, 2500) // poll while ingestion runs
    return () => clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [assistantId])

  const upload = async (e) => {
    const file = e.target.files?.[0]
    if (!file) return
    setUploading(true)
    try {
      await api.uploadAssistantDocument(assistantId, file)
      await load()
    } finally {
      setUploading(false)
      e.target.value = ''
    }
  }

  const StatusIcon = ({ s }) =>
    s === 'ready' ? <CheckCircle size={14} className="text-green-600" />
    : s === 'error' ? <AlertCircle size={14} className="text-red-600" />
    : <Loader2 size={14} className="animate-spin text-accent" />

  if (!assistantId) {
    return (
      <p className="rounded-lg border border-dashed border-border bg-surface px-3 py-3 text-xs text-muted">
        Save the assistant first, then upload knowledge-base documents here.
      </p>
    )
  }

  return (
    <div>
      <label className="mb-2 flex cursor-pointer items-center justify-center gap-2 rounded-lg border-2 border-dashed border-border bg-surface px-3 py-4 text-sm text-content hover:border-accent">
        <input type="file" className="hidden" onChange={upload} disabled={uploading}
          accept=".pdf,.docx,.txt,.md,.markdown,.py,.js,.ts,.json,.csv,.html,.xml,.yaml,.yml" />
        {uploading ? <Loader2 size={16} className="animate-spin text-accent" /> : <Upload size={16} className="text-accent" />}
        {uploading ? 'Uploading…' : 'Upload a document'}
      </label>
      {docs.length === 0 && <p className="text-xs text-muted">No documents yet. Uploaded files are searchable by everyone using this assistant.</p>}
      <div className="space-y-1.5">
        {docs.map((d) => (
          <div key={d.id} className="flex items-center gap-2 rounded-lg border border-border bg-surface px-2.5 py-1.5">
            <FileText size={15} className="shrink-0 text-accent" />
            <div className="min-w-0 flex-1">
              <div className="truncate text-xs text-content">{d.filename}</div>
              <div className="text-[11px] text-muted">
                {(d.size_bytes / 1024).toFixed(0)} KB · {d.n_chunks} chunks{d.error ? ` · ${d.error}` : ''}
              </div>
            </div>
            <StatusIcon s={d.status} />
            <button
              onClick={() => api.deleteAssistantDocument(assistantId, d.id).then(load)}
              className="rounded p-1 text-muted hover:text-red-600"
              title="Delete"
            >
              <Trash2 size={14} />
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}

function AssistantEditor({ assistant, onSaved, onCancel }) {
  const providers = useStore((s) => s.providers)
  const [form, setForm] = useState(() => ({ ...BLANK, ...(assistant || {}) }))
  const [models, setModels] = useState([])
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const isNew = !assistant?.id

  const patch = (p) => setForm((f) => ({ ...f, ...p }))

  useEffect(() => {
    if (!form.profile) {
      setModels([])
      return
    }
    api.getModels(form.profile)
      .then(({ models: m }) => setModels(m || []))
      .catch(() => setModels([]))
  }, [form.profile])

  const onAvatarFile = async (e) => {
    const file = e.target.files?.[0]
    if (!file) return
    try {
      patch({ avatar: await fileToAvatarDataUrl(file) })
    } catch {
      setError('Could not read that image.')
    }
    e.target.value = ''
  }

  const save = async () => {
    setSaving(true)
    setError('')
    try {
      const body = {
        ...form,
        profile: form.profile || null,
        model: form.model || null,
        description: form.description || null,
        system_prompt: form.system_prompt || null,
        prompt_suggestions: (form.prompt_suggestions || []).map((s) => s.trim()).filter(Boolean),
      }
      const saved = isNew
        ? await api.createAssistant(body)
        : await api.updateAssistant(assistant.id, body)
      onSaved(saved)
    } catch (err) {
      setError(String(err.message || err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="rounded-xl border border-border bg-surface-2 p-4">
      <div className="mb-4 flex items-center justify-between">
        <h4 className="text-sm font-semibold text-content">
          {isNew ? 'New assistant' : `Edit ${assistant.name}`}
        </h4>
        <button onClick={onCancel} className="rounded p-1 text-muted hover:text-content" title="Close">
          <X size={16} />
        </button>
      </div>

      <div className="space-y-3">
        <div className="flex items-start gap-4">
          <div className="flex flex-col items-center gap-1.5">
            <AssistantAvatar assistant={form} size={64} />
            <div className="flex gap-1">
              <label className="cursor-pointer rounded p-1 text-muted hover:text-accent" title="Upload avatar image">
                <input type="file" accept="image/*" className="hidden" onChange={onAvatarFile} />
                <ImagePlus size={15} />
              </label>
              {form.avatar && (
                <button onClick={() => patch({ avatar: null })} className="rounded p-1 text-muted hover:text-red-600" title="Remove avatar">
                  <Trash2 size={15} />
                </button>
              )}
            </div>
          </div>
          <div className="flex-1 space-y-3">
            <Field label="Name">
              <input className={inputCls} value={form.name} placeholder="e.g. IT Assistant"
                onChange={(e) => patch({ name: e.target.value })} />
            </Field>
            <Field label="Description" hint="Shown to users on the assistant picker.">
              <input className={inputCls} value={form.description || ''}
                placeholder="e.g. Helps with enterprise architecture questions"
                onChange={(e) => patch({ description: e.target.value })} />
            </Field>
            <Field label="Avatar emoji" hint="Optional alternative to an image (e.g. 🤖).">
              <input className={inputCls} value={form.avatar?.startsWith('data:image/') ? '' : form.avatar || ''}
                placeholder="🤖" maxLength={16}
                onChange={(e) => patch({ avatar: e.target.value || null })} />
            </Field>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <Field label="Provider profile" hint="Leave unset to follow each user's own model settings.">
            <select className={inputCls} value={form.profile || ''}
              onChange={(e) => patch({ profile: e.target.value, model: '' })}>
              <option value="">User's default</option>
              {providers.map((p) => (
                <option key={p.name} value={p.name}>{p.label || p.name}</option>
              ))}
            </select>
          </Field>
          <Field label="Model">
            <select className={inputCls} value={form.model || ''} disabled={!form.profile}
              onChange={(e) => patch({ model: e.target.value })}>
              <option value="">{form.profile ? 'Profile default' : '—'}</option>
              {models.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
          </Field>
        </div>

        <Field label="System prompt" hint="Defines the persona and role. Edits apply to existing chats with this assistant too.">
          <textarea className={`${inputCls} min-h-[110px] resize-y`} value={form.system_prompt || ''}
            placeholder="You are a helpful IT Architecture Assistant named Bridget…"
            onChange={(e) => patch({ system_prompt: e.target.value })} />
        </Field>

        <Field label="Prompt suggestions" hint="Starter prompts shown on the new-chat screen.">
          <div className="space-y-1.5">
            {(form.prompt_suggestions || []).map((s, i) => (
              <div key={i} className="flex gap-1.5">
                <input className={inputCls} value={s}
                  placeholder="e.g. Who are you and what can you help with?"
                  onChange={(e) =>
                    patch({
                      prompt_suggestions: form.prompt_suggestions.map((v, j) => (j === i ? e.target.value : v)),
                    })
                  } />
                <button
                  onClick={() => patch({ prompt_suggestions: form.prompt_suggestions.filter((_, j) => j !== i) })}
                  className="rounded p-1.5 text-muted hover:text-red-600" title="Remove"
                >
                  <X size={14} />
                </button>
              </div>
            ))}
            <button
              onClick={() => patch({ prompt_suggestions: [...(form.prompt_suggestions || []), ''] })}
              className="flex items-center gap-1 rounded px-1 py-0.5 text-xs text-accent hover:underline"
            >
              <Plus size={13} /> Add suggestion
            </button>
          </div>
        </Field>

        <Field label="Capabilities" hint="Unchecked capabilities are blocked for everyone using this assistant.">
          <div className="flex flex-wrap gap-x-5 gap-y-1.5">
            {CAPABILITIES.map((c) => (
              <label key={c.key} className="flex cursor-pointer items-center gap-1.5 text-sm text-content" title={c.hint}>
                <input type="checkbox"
                  checked={form.capabilities?.[c.key] !== false}
                  onChange={(e) =>
                    patch({ capabilities: { ...form.capabilities, [c.key]: e.target.checked } })
                  }
                  className="rounded border-border text-accent focus:ring-accent" />
                {c.label}
              </label>
            ))}
          </div>
        </Field>

        <Field label="Visibility">
          <select className={inputCls} value={form.visibility}
            onChange={(e) => patch({ visibility: e.target.value })}>
            <option value="public">Public — all users can chat with it</option>
            <option value="private">Private — only you (and admins)</option>
          </select>
        </Field>

        <Field label="Knowledge base" hint="Documents every user of this assistant can search. Model choice is fixed per chat at creation; prompt edits apply everywhere.">
          <KnowledgeSection assistantId={assistant?.id} />
        </Field>

        {error && <p className="text-xs text-red-600">{error}</p>}

        <div className="flex justify-end gap-2 pt-1">
          <button onClick={onCancel} className="rounded-lg border border-border px-3 py-1.5 text-sm text-content hover:bg-surface-3">
            Cancel
          </button>
          <button onClick={save} disabled={saving || !form.name.trim()}
            className="rounded-lg bg-accent px-4 py-1.5 text-sm font-medium text-accent-fg hover:opacity-90 disabled:opacity-40">
            {saving ? 'Saving…' : isNew ? 'Create assistant' : 'Save changes'}
          </button>
        </div>
      </div>
    </div>
  )
}

export default function AssistantsPanel() {
  const [assistants, setAssistants] = useState([])
  const [editing, setEditing] = useState(null) // null | 'new' | assistant object
  const loadGlobal = useStore((s) => s.loadAssistants)

  const load = () => api.listAssistants().then(setAssistants).catch(() => setAssistants([]))

  useEffect(() => {
    load()
  }, [])

  const refresh = async () => {
    await load()
    loadGlobal() // keep the chat picker in sync
  }

  const remove = async (a) => {
    if (!window.confirm(`Delete "${a.name}" and its knowledge base? Existing chats keep working with their saved settings.`)) return
    await api.deleteAssistant(a.id)
    if (editing?.id === a.id) setEditing(null)
    await refresh()
  }

  return (
    <div>
      <div className="mb-1 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-content">Assistants</h3>
        {!editing && (
          <button onClick={() => setEditing('new')}
            className="flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-accent-fg hover:opacity-90">
            <Plus size={15} /> New assistant
          </button>
        )}
      </div>
      <p className="mb-4 text-xs text-muted">
        Curated personas built on your configured models: a custom system prompt, an optional
        knowledge base, starter prompts, and capability limits. Users pick one when starting a chat.
      </p>

      {editing && (
        <div className="mb-4">
          <AssistantEditor
            assistant={editing === 'new' ? null : editing}
            onSaved={async (saved) => {
              await refresh()
              // Keep the editor open on create so the admin can upload KB documents.
              setEditing(editing === 'new' ? saved : null)
            }}
            onCancel={() => setEditing(null)}
          />
        </div>
      )}

      <div className="space-y-2">
        {assistants.length === 0 && !editing && (
          <div className="flex flex-col items-center rounded-xl border border-dashed border-border bg-surface px-4 py-8 text-center">
            <Bot size={22} className="mb-2 text-accent" />
            <p className="text-sm text-content">No assistants yet.</p>
            <p className="text-xs text-muted">Create one to give your users a purpose-built persona.</p>
          </div>
        )}
        {assistants.map((a) => (
          <div key={a.id} className="flex items-center gap-3 rounded-lg border border-border bg-surface px-3 py-2">
            <AssistantAvatar assistant={a} size={34} />
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="truncate text-sm font-medium text-content">{a.name}</span>
                {a.visibility === 'private' && (
                  <span className="rounded bg-surface-3 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted">private</span>
                )}
              </div>
              <div className="truncate text-xs text-muted">
                {a.model || 'user default model'} · {a.n_documents} doc{a.n_documents === 1 ? '' : 's'}
                {a.description ? ` · ${a.description}` : ''}
              </div>
            </div>
            <button onClick={() => setEditing(a)} className="rounded p-1.5 text-muted hover:text-accent" title="Edit">
              <Pencil size={15} />
            </button>
            <button onClick={() => remove(a)} className="rounded p-1.5 text-muted hover:text-red-600" title="Delete">
              <Trash2 size={15} />
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}
