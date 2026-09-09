import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import { useStore } from '../../store/useStore'

const blank = () => ({ name: '', description: '', instructions: '', document_ids: [], archived: false })
const field = 'mt-1 w-full rounded-lg border-border bg-surface px-3 py-2 text-sm text-content focus:border-accent focus:ring-accent'

export default function ProjectsPanel() {
  const projects = useStore(s => s.projects)
  const load = useStore(s => s.loadProjects)
  const activeId = useStore(s => s.activeId)
  const openProject = useStore(s => s.openProject)
  const selectConversation = useStore(s => s.selectConversation)
  const move = useStore(s => s.moveConversation)
  const [id, setId] = useState('')
  const [form, setForm] = useState(blank)
  const [docs, setDocs] = useState([])
  const [chats, setChats] = useState([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const patch = values => setForm(old => ({ ...old, ...values }))
  useEffect(() => { api.listDocuments().then(rows => setDocs(rows.filter(d => !d.conversation_id && !d.assistant_id))).catch(() => setError('Could not load library documents.')) }, [])
  useEffect(() => {
    let current = true
    setError(''); setChats([])
    if (!id) { setForm(blank()); setBusy(false); return }
    setBusy(true)
    api.getProject(id).then(data => { if (current) { setForm(data); setChats(data.conversations) } })
      .catch(e => { if (current) setError(e.message) }).finally(() => { if (current) setBusy(false) })
    return () => { current = false }
  }, [id])
  const save = async () => {
    setBusy(true); setError(''); setNotice('')
    try {
      const body = { name: form.name, description: form.description, instructions: form.instructions,
        document_ids: form.document_ids, archived: form.archived, revision: form.revision }
      const fresh = id ? await api.updateProject(id, body) : await api.createProject(body)
      setForm(fresh); await load(); setId(fresh.id); setNotice('Project saved.')
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }
  const action = async fn => { setError(''); try { await fn(); setNotice('Chat updated. Close Settings to continue.') } catch(e) { setError(e.message) } }
  return <section aria-label="Projects" className="space-y-4 text-sm text-content">
    <h3 className="font-semibold">Projects</h3>
    <p className="text-xs text-muted">Private workspaces for related chats, selected library documents, and shared instructions. An assistant remains a reusable persona.</p>
    <select aria-label="Edit project" value={id} onChange={e => setId(e.target.value)} className={field}>
      <option value="">Create a project</option>
      {projects.map(p => <option key={p.id} value={p.id}>{p.name}{p.archived ? ' (archived)' : ''}</option>)}
    </select>
    <fieldset disabled={busy} className="space-y-3 disabled:opacity-60">
      <label className="block">Name<input className={field} value={form.name} maxLength={120} onChange={e => patch({ name: e.target.value })} /></label>
      <label className="block">Description<textarea className={field} rows={2} value={form.description} maxLength={2000} onChange={e => patch({ description: e.target.value })} /></label>
      <label className="block">Project instructions<textarea className={field} rows={5} value={form.instructions} maxLength={8000} onChange={e => patch({ instructions: e.target.value })} /></label>
      <div><h4 className="mb-1 font-medium">Project knowledge</h4>
        <p className="mb-2 text-xs text-muted">Choose up to 32 library documents. Upload files through Settings → Documents. Chat-specific attachments remain in their original chat.</p>
        <div className="max-h-48 space-y-2 overflow-y-auto rounded-lg border border-border p-3">
          {!docs.length && <p className="text-xs text-muted">No library documents yet.</p>}
          {docs.map(d => <label key={d.id} className="flex items-start gap-2">
            <input type="checkbox" checked={form.document_ids.includes(d.id)} onChange={e => patch({ document_ids: e.target.checked ? [...form.document_ids, d.id] : form.document_ids.filter(x => x !== d.id) })} className="mt-0.5 rounded border-border text-accent focus:ring-accent" />
            <span className="min-w-0 break-words">{d.filename} <span className="text-xs text-muted">{d.status}</span></span>
          </label>)}
        </div>
      </div>
      {id && <label className="flex items-center gap-2"><input type="checkbox" checked={form.archived} onChange={e => patch({ archived: e.target.checked })} className="rounded text-accent" /> Archived (preserves chats and documents)</label>}
      <button onClick={save} disabled={!form.name.trim() || form.document_ids.length > 32} className="rounded-lg bg-accent px-4 py-2 text-accent-fg disabled:opacity-50">{busy ? 'Saving…' : 'Save project'}</button>
    </fieldset>
    {error && <p role="alert" className="break-words">{error}</p>}
    {notice && <p role="status" className="text-accent">{notice}</p>}
    {id && !form.archived && <div className="flex flex-wrap gap-2">
      <button onClick={() => action(() => openProject(id))} className="rounded border border-border px-3 py-2">New chat in project</button>
      {activeId && <button onClick={() => action(() => move(id))} className="rounded border border-border px-3 py-2">Move current chat here</button>}
    </div>}
    {activeId && <button onClick={() => action(() => move(null))} className="text-xs text-muted underline">Remove current chat from its project</button>}
    {id && <div className="space-y-2"><h4 className="font-medium">Recent chats</h4>
      {!chats.length && <p className="text-xs text-muted">No chats in this project yet.</p>}
      {chats.slice(0, 20).map(c => <button key={c.id} onClick={() => action(() => selectConversation(c.id))} className="block w-full rounded border border-border p-2 text-left hover:border-accent">{c.title}</button>)}
    </div>}
  </section>
}
