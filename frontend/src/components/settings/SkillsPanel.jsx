import { useEffect, useState } from 'react'
import { Sparkles, Plus, Trash2, Pencil, X, Upload, Download, Loader2 } from 'lucide-react'
import { api } from '../../api/client'
import { useStore } from '../../store/useStore'
import { authHeaders } from '../../api/token'

const BLANK = {
  name: '',
  description: '',
  instructions: '',
  auto_activate: true,
  visibility: 'private',
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

export default function SkillsPanel() {
  const user = useStore((s) => s.user)
  const refreshStoreSkills = useStore((s) => s.loadSkills)
  const isAdmin = user?.role === 'admin'
  const [skills, setSkills] = useState([])
  const [editing, setEditing] = useState(null) // null | {id?, ...form}
  const [saving, setSaving] = useState(false)
  const [importing, setImporting] = useState(false)
  const [error, setError] = useState(null)

  const load = () => api.listSkills().then(setSkills).catch(() => setSkills([]))
  useEffect(() => { load() }, [])

  const refresh = async () => {
    await load()
    refreshStoreSkills() // keep the composer "/" picker in sync
  }

  const save = async () => {
    if (!editing?.name.trim() || !editing?.description.trim()) return
    setSaving(true)
    setError(null)
    try {
      const body = {
        name: editing.name,
        description: editing.description,
        instructions: editing.instructions,
        auto_activate: editing.auto_activate,
        visibility: editing.visibility,
      }
      if (editing.id) await api.updateSkill(editing.id, body)
      else await api.createSkill(body)
      setEditing(null)
      await refresh()
    } catch (e) {
      setError(String(e.message || e))
    } finally {
      setSaving(false)
    }
  }

  const remove = async (skill) => {
    if (!window.confirm(`Delete the skill "/${skill.name}"?`)) return
    await api.deleteSkill(skill.id)
    await refresh()
  }

  const onImport = async (e) => {
    const file = e.target.files?.[0]
    if (!file) return
    setImporting(true)
    setError(null)
    try {
      await api.importSkill(file)
      await refresh()
    } catch (err) {
      setError(String(err.message || err))
    } finally {
      setImporting(false)
      e.target.value = ''
    }
  }

  const exportSkill = async (skill) => {
    const res = await fetch(api.exportSkillUrl(skill.id), { headers: { ...authHeaders() } })
    if (!res.ok) return
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${skill.name}-SKILL.md`
    a.click()
    URL.revokeObjectURL(url)
  }

  const canEdit = (skill) => isAdmin || skill.created_by === user?.username || skill.created_by === user?.id

  return (
    <div>
      <h3 className="mb-1 text-sm font-semibold text-content">Skills</h3>
      <p className="mb-4 text-xs text-muted">
        Reusable instructions the agent follows for specialized tasks. Invoke one explicitly by
        typing <code className="rounded bg-surface-3 px-1">/</code> in the composer, or let the
        agent load relevant skills on its own (the <em>Skills</em> toggle under the composer).
        Compatible with the Anthropic{' '}
        <code className="rounded bg-surface-3 px-1">SKILL.md</code> format for import/export.
      </p>

      {error && (
        <div className="mb-3 rounded-lg border border-red-300 bg-red-50 px-3 py-2 text-xs text-red-700">
          {error}
        </div>
      )}

      <div className="mb-4 flex gap-2">
        <button
          onClick={() => { setError(null); setEditing({ ...BLANK }) }}
          className="flex items-center gap-1.5 rounded-lg bg-accent px-3 py-2 text-sm text-accent-fg hover:opacity-90"
        >
          <Plus size={15} /> New skill
        </button>
        <label className="flex cursor-pointer items-center gap-1.5 rounded-lg border border-border bg-surface px-3 py-2 text-sm text-content hover:border-accent">
          <input type="file" accept=".md,.markdown,text/markdown" className="hidden" onChange={onImport} disabled={importing} />
          {importing ? <Loader2 size={15} className="animate-spin" /> : <Upload size={15} />}
          Import SKILL.md
        </label>
      </div>

      {editing && (
        <div className="mb-4 space-y-3 rounded-xl border border-border bg-surface p-4">
          <div className="flex items-center justify-between">
            <h4 className="text-sm font-semibold text-content">
              {editing.id ? `Edit /${editing.name}` : 'New skill'}
            </h4>
            <button onClick={() => setEditing(null)} className="rounded p-1 text-muted hover:text-content">
              <X size={15} />
            </button>
          </div>
          <Field label="Name" hint="Lowercase slug; users invoke it as /name.">
            <input value={editing.name} onChange={(e) => setEditing({ ...editing, name: e.target.value })}
              placeholder="data-analysis" className={inputCls} />
          </Field>
          <Field
            label="Description"
            hint="What it does AND when to use it — this is how the model decides to activate the skill."
          >
            <textarea value={editing.description} rows={2}
              onChange={(e) => setEditing({ ...editing, description: e.target.value })}
              placeholder="Structured analysis of tabular data. Use when the user asks to analyze a dataset."
              className={inputCls} />
          </Field>
          <Field label="Instructions" hint="Markdown workflow the agent follows once the skill is active.">
            <textarea value={editing.instructions} rows={10}
              onChange={(e) => setEditing({ ...editing, instructions: e.target.value })}
              placeholder={'# My Skill\n\n1. Do this first…'}
              className={`${inputCls} font-mono text-xs`} />
          </Field>
          <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-content">
            <label className="flex cursor-pointer items-center gap-1.5" title="List this skill in the agent's prompt so it can load it on its own">
              <input type="checkbox" checked={editing.auto_activate}
                onChange={(e) => setEditing({ ...editing, auto_activate: e.target.checked })}
                className="rounded border-border text-accent focus:ring-accent" />
              Agent may auto-activate
            </label>
            {isAdmin && (
              <label className="flex items-center gap-1.5">
                Visibility
                <select value={editing.visibility}
                  onChange={(e) => setEditing({ ...editing, visibility: e.target.value })}
                  className="rounded-lg border-border bg-surface text-xs text-content focus:border-accent focus:ring-accent">
                  <option value="private">private (only me)</option>
                  <option value="public">public (everyone)</option>
                </select>
              </label>
            )}
          </div>
          <div className="flex justify-end gap-2">
            <button onClick={() => setEditing(null)} className="rounded-lg px-3 py-1.5 text-xs text-muted hover:text-content">
              Cancel
            </button>
            <button onClick={save} disabled={saving || !editing.name.trim() || !editing.description.trim()}
              className="rounded-lg bg-accent px-3 py-1.5 text-xs text-accent-fg hover:opacity-90 disabled:opacity-50">
              {saving ? 'Saving…' : editing.id ? 'Save changes' : 'Create skill'}
            </button>
          </div>
        </div>
      )}

      <div className="space-y-2">
        {skills.length === 0 && <p className="text-sm text-muted">No skills yet.</p>}
        {skills.map((s) => (
          <div key={s.id} className="flex items-start gap-3 rounded-lg border border-border bg-surface px-3 py-2">
            <Sparkles size={16} className="mt-0.5 shrink-0 text-accent" />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium text-content">/{s.name}</span>
                <span className={`rounded px-1.5 py-0.5 text-[10px] ${
                  s.visibility === 'public' ? 'bg-hutch-cyan/15 text-hutch-cyan' : 'bg-surface-3 text-muted'
                }`}>
                  {s.visibility}
                </span>
                {s.auto_activate && (
                  <span className="rounded bg-hutch-purple/15 px-1.5 py-0.5 text-[10px] text-hutch-purple">
                    auto
                  </span>
                )}
              </div>
              <div className="mt-0.5 line-clamp-2 text-xs text-muted">{s.description}</div>
            </div>
            <div className="flex shrink-0 items-center gap-1">
              <button onClick={() => exportSkill(s)} className="rounded p-1.5 text-muted hover:text-accent" title="Export SKILL.md">
                <Download size={15} />
              </button>
              {canEdit(s) && (
                <>
                  <button onClick={() => { setError(null); setEditing({ ...BLANK, ...s }) }}
                    className="rounded p-1.5 text-muted hover:text-accent" title="Edit">
                    <Pencil size={15} />
                  </button>
                  <button onClick={() => remove(s)} className="rounded p-1.5 text-muted hover:text-red-600" title="Delete">
                    <Trash2 size={15} />
                  </button>
                </>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
