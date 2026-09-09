import { useEffect, useRef, useState } from 'react'
import { Download, RefreshCw } from 'lucide-react'
import { api } from '../../api/client'
import { streamChat } from '../../api/sse'
import { useStore } from '../../store/useStore'
import Markdown from '../markdown/Markdown'

export const draftKey = canvas => `${canvas.conversationId}:${canvas.path}:${canvas.savedUrl || 'workspace'}`
const button = 'rounded border border-border px-2 py-1 text-xs text-content hover:bg-surface-3 disabled:opacity-40'
const primary = `${button} bg-accent !text-accent-fg`

// Textareas expose LF-normalized offsets even when the saved file contains CRLF.
function originalOffset(text, offset) {
  let index = 0
  for (let count = 0; count < offset && index < text.length; count++) {
    index += text[index] === '\r' && text[index + 1] === '\n' ? 2 : 1
  }
  return index
}

export default function ArtifactEditor({ canvas }) {
  const key = draftKey(canvas)
  const cid = canvas.conversationId
  const [info, setInfo] = useState(null)
  const [draft, setDraft] = useState('')
  const [expectedHead, setExpectedHead] = useState(null)
  const [view, setView] = useState('edit')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [compare, setCompare] = useState('')
  const [difference, setDifference] = useState(null)
  const [selection, setSelection] = useState(null)
  const [instruction, setInstruction] = useState('')
  const [proposal, setProposal] = useState(null)
  const [revising, setRevising] = useState(false)
  const selectedModel = useStore(s => s.settings?.model)
  const textarea = useRef(null)
  const stop = useRef(null)
  const generation = useRef(0)
  const alive = useRef(true)
  const dirty = !!info && draft !== info.version.content

  useEffect(() => {
    alive.current = true
    useStore.setState({ canvasEditing: true })
    const cached = useStore.getState().artifactDrafts[key]
    const saved = canvas.savedUrl?.match(/\/saved\/([^/]+)\/(\d+)$/)
    const request = cached
      ? api.getArtifact(cid, cached.artifactId, cached.baseVersion)
      : canvas.artifactId
        ? api.getArtifact(cid, canvas.artifactId, canvas.versionId)
        : api.openArtifact(cid, { path: canvas.path, ...(saved ? { message_id: saved[1], snapshot_index: Number(saved[2]) } : {}) })
    let cancelled = false
    request.then(data => {
      if (cancelled) return
      setInfo(data)
      setDraft(cached?.content ?? data.version.content)
      setExpectedHead(cached?.expectedHead ?? data.head_version_id)
      setCompare(data.version.parent_version_id || '')
      if (cached) setNotice('Your unsaved draft has been restored. Save it before leaving this browser session.')
    }).catch(e => { if (!cancelled) setError(e.message) })
    return () => {
      cancelled = true
      alive.current = false
      generation.current++
      stop.current?.()
      useStore.setState({ canvasEditing: false })
    }
  }, [key, cid, canvas.artifactId, canvas.versionId, canvas.savedUrl, canvas.path])

  useEffect(() => {
    if (view !== 'compare' || !info || !compare) return
    let cancelled = false
    setDifference(null)
    api.diffArtifact(cid, info.id, compare, info.version.id).then(result => {
      if (!cancelled) setDifference(result)
    }).catch(e => { if (!cancelled) setError(e.message) })
    return () => { cancelled = true }
  }, [view, compare, info?.id, info?.version.id, cid])

  const remember = (value, base = info, head = expectedHead) => {
    useStore.getState().keepArtifactDraft(key, value === base.version.content ? null : {
      content: value, artifactId: base.id, baseVersion: base.version.id, expectedHead: head,
    })
  }
  const change = value => {
    setDraft(value)
    remember(value)
    setSelection(null)
    setProposal(null)
    setNotice('')
  }
  const adopt = data => {
    setInfo(data)
    setDraft(data.version.content)
    setExpectedHead(data.head_version_id)
    setCompare(data.version.parent_version_id || '')
    setSelection(null)
    setProposal(null)
    useStore.getState().keepArtifactDraft(key, null)
  }
  const work = async action => {
    setBusy(true)
    setError('')
    setNotice('')
    try { await action() } catch (e) { if (alive.current) setError(e.message) }
    finally { if (alive.current) setBusy(false) }
  }
  const selectVersion = id => {
    if (dirty && !window.confirm('Discard this unsaved draft and open another version?')) return
    work(async () => {
      const data = await api.getArtifact(cid, info.id, id)
      if (alive.current) adopt(data)
    })
  }
  const save = () => work(async () => {
    const data = await api.saveArtifact(cid, info.id, {
      expected_head: expectedHead, base_version_id: info.version.id, content: draft,
    })
    if (!alive.current) return
    // Keep the original workspace observation so changes during editing cannot be
    // silently accepted as the expected value for the later publication action.
    adopt({ ...data, workspace: info.workspace })
    setNotice(`Saved version ${data.version.number}. Use in workspace when you want the agent to use this file.`)
  })
  const reload = () => work(async () => {
    const data = await api.getArtifact(cid, info.id, info.version.id)
    if (!alive.current) return
    setInfo(data)
    setExpectedHead(data.head_version_id)
    remember(draft, data, data.head_version_id)
    setNotice('Version list and workspace status refreshed. Your draft is unchanged; compare newer versions before saving.')
  })
  const restore = () => work(async () => {
    const data = await api.restoreArtifact(cid, info.id, { expected_head: expectedHead, version_id: info.version.id })
    if (!alive.current) return
    adopt({ ...data, workspace: info.workspace })
    setNotice(`Restored as version ${data.version.number}. Previous versions and the workspace are unchanged.`)
  })
  const publish = () => work(async () => {
    const workspace = await api.publishArtifact(cid, info.id, {
      expected_head: expectedHead, version_id: info.version.id, expected_workspace_sha256: info.workspace.sha256,
    })
    if (!alive.current) return
    setInfo({ ...info, workspace })
    setNotice('Workspace updated. Future agent tools will read this version; saved answers are unchanged.')
  })
  const revise = () => {
    if (!selection || dirty || !instruction.trim()) return
    const token = ++generation.current
    setRevising(true)
    setError('')
    setProposal(null)
    setNotice('')
    let received = false
    const current = () => alive.current && generation.current === token
    stop.current = streamChat({ expected_head: expectedHead, version_id: info.version.id,
      start: Array.from(draft.slice(0, selection.start)).length,
      end: Array.from(draft.slice(0, selection.end)).length, instruction,
    }, event => {
      if (!current()) return
      if (event.type === 'artifact_proposal') {
        received = true
        setProposal({ ...event, selection })
      } else if (event.type === 'error') {
        received = true
        setError(event.content)
      }
    }, () => {
      if (!current()) return
      setRevising(false)
      if (!received) setError('No complete proposal was received. Your draft is unchanged.')
    }, e => { if (current()) { setError(e.message); setRevising(false) } }, `/api/artifacts/${cid}/${info.id}/revise`)
  }
  const cancelRevision = () => {
    generation.current++
    stop.current?.()
    setRevising(false)
    setNotice('Revision stopped. Your document is unchanged.')
  }
  const applyProposal = () => {
    const { start, end } = proposal.selection
    change(draft.slice(0, start) + proposal.replacement + draft.slice(end))
    setNotice('Proposal applied to your draft. Review and save a new version.')
    textarea.current?.focus()
  }
  const captureSelection = event => {
    const { selectionStart: start, selectionEnd: end } = event.currentTarget
    if (!revising && !proposal) setSelection(end > start ? {
      start: originalOffset(draft, start), end: originalOffset(draft, end),
    } : null)
  }

  if (!info) return <div className="p-4 text-sm text-muted" role="status">{error || 'Opening version history…'}</div>
  const current = info.version.id === info.head_version_id
  const locked = busy || revising
  const download = `/api/artifacts/${cid}/${info.id}/download/${info.version.id}`
  const dot = canvas.name.lastIndexOf('.')
  const downloadName = dot > 0 ? `${canvas.name.slice(0, dot)}-v${info.version.number}${canvas.name.slice(dot)}` : `${canvas.name}-v${info.version.number}`
  const workspaceMatches = info.workspace.available && info.workspace.sha256 === info.version.sha256

  return <div className="flex h-full min-h-0 flex-col">
    <div className="space-y-2 border-b border-border px-3 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <select aria-label="Artifact version" disabled={locked} value={info.version.id}
          onChange={e => selectVersion(e.target.value)} className="min-w-0 max-w-full flex-1 rounded border-border bg-surface-2 py-1 text-xs text-content">
          {info.versions.map(v => <option key={v.id} value={v.id}>Version {v.number} · {v.origin}{v.id === info.head_version_id ? ' · current' : ''}</option>)}
        </select>
        <button className={button} disabled={locked} onClick={reload} aria-label="Reload versions" title="Reload versions">
          <RefreshCw size={16} className="md:hidden" /><span className="hidden md:inline">Reload versions</span>
        </button>
        <button className={button} disabled={locked} aria-label="Download version" title="Download version"
          onClick={() => api.downloadFile(download, downloadName).catch(e => setError(e.message))}>
          <Download size={16} className="md:hidden" /><span className="hidden md:inline">Download version</span>
        </button>
      </div>
      <div className="flex flex-wrap items-center gap-1">
        {['edit', 'preview', 'compare'].map(tab => <button key={tab} className={view === tab ? primary : button}
          onClick={() => setView(tab)} aria-pressed={view === tab}>{tab[0].toUpperCase() + tab.slice(1)}</button>)}
        <span className="flex-1" />
        <button className={primary} onClick={save} disabled={locked || !dirty}>Save version</button>
        {!current && <button className={button} onClick={restore} disabled={locked || dirty}>Restore as new version</button>}
      </div>
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
        <span className="flex-1">{dirty ? 'Unsaved draft · retained while switching chats' : workspaceMatches ? 'This version matches the workspace' : 'Workspace differs from this version'}</span>
        <button className={button} disabled={locked || dirty || !current || !info.workspace.available || workspaceMatches}
          onClick={publish} title="Replace the workspace file with this saved version; fails if the file has changed">Use in workspace</button>
      </div>
      {info.version.source_message_id && <p className="text-xs text-muted">Derived from a saved answer. Edits are not automatically checked against its sources.</p>}
      {error && <p role="alert" className="break-words text-xs text-red-500">{error}</p>}
      {notice && <p role="status" className="text-xs text-muted">{notice}</p>}
    </div>

    {view === 'edit' ? <>
      <textarea ref={textarea} aria-label="Artifact text" spellCheck={false} maxLength={1048576} value={draft} disabled={locked}
        onChange={e => change(e.target.value)}
        onSelect={captureSelection} onBlur={captureSelection}
        className="min-h-32 w-full flex-1 resize-none border-0 bg-surface p-4 font-mono text-sm text-content focus:ring-1 focus:ring-inset focus:ring-accent" />
      <div className="max-h-[50%] shrink-0 space-y-2 overflow-y-auto border-t border-border bg-surface-2 p-3">
        {proposal ? <>
          <p className="text-xs font-medium text-content">Proposed replacement · {proposal.model}</p>
          <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words rounded border border-border bg-surface p-2 text-xs text-content">{proposal.replacement}</pre>
          <div className="flex flex-wrap items-center gap-2">
            <button className={primary} disabled={revising} onClick={applyProposal}>Apply to draft</button>
            <button className={button} onClick={() => setProposal(null)}>Discard proposal</button>
            <span className="text-xs text-muted">{proposal.usage?.total ?? 'Unknown'} tokens · review before saving</span>
          </div>
        </> : <>
          <p className="text-xs text-muted">{dirty ? 'Save your draft before requesting an AI revision.' : selection
            ? `${Array.from(draft.slice(selection.start, selection.end)).length.toLocaleString()} characters selected`
            : 'Select a passage above to revise it with AI.'} Only the selected text and instruction are sent to {selectedModel || 'your selected model'}.</p>
          <div className="flex gap-2">
            <input aria-label="Revision instruction" placeholder="e.g. Make this paragraph more concise" value={instruction} maxLength={2000}
              onChange={e => setInstruction(e.target.value)} disabled={locked}
              className="min-w-0 flex-1 rounded border-border bg-surface text-xs text-content" />
            {revising ? <button className={button} onClick={cancelRevision}>Stop revision</button>
              : <button className={button} onClick={revise} disabled={locked || dirty || !selection || !instruction.trim() || Array.from(draft.slice(selection?.start, selection?.end)).length > 12000}>Revise selection</button>}
          </div>
        </>}
      </div>
    </> : view === 'compare' ? <div className="min-h-0 flex-1 overflow-auto p-3">
      <p className="mb-2 text-xs text-muted">Compare saved versions. Unsaved draft changes are not included.</p>
      <label className="text-xs text-muted">Compare with <select aria-label="Compare version" value={compare} onChange={e => setCompare(e.target.value)}
        className="ml-2 rounded border-border bg-surface-2 py-1 text-xs text-content">
        <option value="">Choose a version</option>
        {info.versions.filter(v => v.id !== info.version.id).map(v => <option key={v.id} value={v.id}>Version {v.number}</option>)}
      </select></label>
      {compare && <pre aria-label="Version difference" className="mt-3 whitespace-pre-wrap break-words font-mono text-xs text-content">{difference ? difference.diff || 'No text differences.' : 'Comparing…'}</pre>}
      {difference?.truncated && <p className="mt-2 text-xs text-muted">Comparison truncated. Download both versions for the complete text.</p>}
    </div> : canvas.kind === 'html' ? <iframe title={`${canvas.name} draft preview`} srcDoc={draft}
      sandbox="allow-scripts allow-forms allow-popups allow-modals" className="min-h-0 w-full flex-1 border-0 bg-white" />
      : <div className="min-h-0 flex-1 overflow-auto p-4">{canvas.kind === 'markdown' ? <Markdown>{draft}</Markdown>
        : <pre className="whitespace-pre-wrap break-words font-mono text-xs text-content">{draft}</pre>}</div>}
  </div>
}
