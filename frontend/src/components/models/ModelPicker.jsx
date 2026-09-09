import { useEffect, useId, useRef, useState } from 'react'
import { Check, ChevronDown, Loader2, RefreshCw, Search } from 'lucide-react'
import { api } from '../../api/client'

function describe(row) {
  return [row.parameter_size, row.quantization,
    row.size_bytes ? `${(row.size_bytes / 1024 ** 3).toFixed(1)} GiB` : null,
    row.loaded === true ? 'Loaded' : row.loaded === false ? 'Downloaded · not loaded' : null,
  ].filter(Boolean).join(' · ')
}

// One picker for user settings, assistant models, and unsaved admin profiles.
export default function ModelPicker({ profile, value, onChange, allowDefault = false,
  loadCatalog, revision = '', autoLoad = true, label = 'Model', disabled = false }) {
  const [data, setData] = useState(null)
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const [error, setError] = useState('')
  const root = useRef(null)
  const trigger = useRef(null)
  const input = useRef(null)
  const sequence = useRef(0)
  const loader = useRef(loadCatalog)
  loader.current = loadCatalog
  const listId = useId()

  const refresh = async () => {
    if (!profile) return
    const request = ++sequence.current
    setBusy(true)
    setError('')
    try {
      const result = loader.current ? await loader.current() : await api.getModels(profile, true)
      if (request !== sequence.current) return
      setData(result)
    } catch {
      if (request === sequence.current) setError('Could not refresh models. Check your connection or provider configuration, then try again.')
    } finally {
      if (request === sequence.current) setBusy(false)
    }
  }

  useEffect(() => {
    sequence.current++
    setData(null)
    setError('')
    setBusy(false)
    setOpen(false)
    setQuery('')
    if (autoLoad && profile) refresh()
    return () => { sequence.current++ }
  }, [profile, revision, autoLoad])

  useEffect(() => {
    if (!open) return
    input.current?.focus()
    const outside = e => { if (!root.current?.contains(e.target)) setOpen(false) }
    document.addEventListener('pointerdown', outside)
    return () => document.removeEventListener('pointerdown', outside)
  }, [open])

  useEffect(() => { setActive(0) }, [query, data])

  const items = data?.items || (data?.models || []).map(id => ({ id, name: id }))
  const selected = items.find(row => row.id === value)
  const choices = items.filter(row => row.kind !== 'embedding').map(row => ({ ...row, text: row.name || row.id }))
  if (value && !items.some(row => row.id === value)) choices.unshift({ id: value, text: value, discovered: false })
  if (allowDefault) choices.unshift({ id: '', text: 'Profile default' })
  const term = query.trim().toLowerCase()
  const filtered = choices.filter(row => `${row.text} ${row.id}`.toLowerCase().includes(term))
  const typed = query.trim()
  if (typed && !items.some(row => row.id === typed) && typed !== value) {
    filtered.push({ id: typed, text: `Use custom ID: ${typed}`, custom: true })
  }
  const choose = row => {
    onChange(row.id)
    setOpen(false)
    setQuery('')
    trigger.current?.focus()
  }
  const close = () => { setOpen(false); trigger.current?.focus() }
  const keyDown = e => {
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close() }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      const next = Math.max(0, Math.min(filtered.length - 1, active + (e.key === 'ArrowDown' ? 1 : -1)))
      setActive(next)
      document.getElementById(`${listId}-${next}`)?.scrollIntoView({ block: 'nearest' })
    }
    if (e.key === 'Enter' && filtered[active]) { e.preventDefault(); choose(filtered[active]) }
  }

  return <div ref={root} className="min-w-0" onBlur={e => {
    if (!e.currentTarget.contains(e.relatedTarget)) setOpen(false)
  }}>
    <div className="relative">
      <div className="flex gap-2">
        <button ref={trigger} type="button" aria-label={`Choose ${label.toLowerCase()}`} aria-haspopup="listbox"
          aria-expanded={open} aria-controls={open ? listId : undefined} disabled={!profile || disabled}
          onClick={() => { if (open) close(); else { setOpen(true); setQuery(''); refresh() } }}
          className="flex min-w-0 flex-1 items-center justify-between gap-2 rounded-lg border border-border bg-surface px-3 py-2 text-left text-sm text-content hover:border-accent focus-visible:outline focus-visible:outline-accent disabled:opacity-50">
          <span className="truncate" title={value}>{selected?.name || value || (allowDefault ? 'Profile default' : 'Select a model')}</span>
          <ChevronDown size={15} className="shrink-0 text-muted" />
        </button>
        <button type="button" onClick={refresh} disabled={!profile || busy}
          aria-label="Refresh models" title="Refresh model catalog (no generation or downloads)"
          className="shrink-0 rounded-lg border border-border px-2 text-muted hover:text-accent focus-visible:outline focus-visible:outline-accent disabled:opacity-50">
          {busy ? <Loader2 size={16} className="animate-spin" /> : <RefreshCw size={16} />}
        </button>
      </div>
      {open && <div className="absolute left-0 right-0 top-full z-50 mt-1 rounded-lg border border-border bg-surface p-2 shadow-xl">
        <div className="flex items-center gap-2 px-1">
          <Search size={14} className="shrink-0 text-muted" />
          <input ref={input} role="combobox" aria-label="Filter models" aria-expanded="true" aria-autocomplete="list"
            aria-controls={listId} aria-activedescendant={filtered[active] ? `${listId}-${active}` : undefined}
            value={query} onChange={e => setQuery(e.target.value)} onKeyDown={keyDown}
            placeholder="Search or enter a model ID…" maxLength={512}
            className="min-w-0 flex-1 rounded border-border bg-surface-2 px-2 py-1.5 text-sm text-content focus:border-accent focus:ring-accent" />
        </div>
        <ul id={listId} role="listbox" aria-label="Available models" className="mt-2 max-h-56 overflow-y-auto">
          {filtered.map((row, index) => <li key={row.id || '__default'} id={`${listId}-${index}`} role="option"
            aria-selected={row.id === value} onMouseDown={e => e.preventDefault()} onClick={() => choose(row)}
            onMouseEnter={() => setActive(index)}
            className={`cursor-pointer rounded px-2 py-2 text-sm text-content ${index === active ? 'bg-surface-2' : 'hover:bg-surface-2'}`}>
            <div className="flex items-center gap-2"><span className="min-w-0 flex-1 break-words">{row.text}</span>{row.id === value && <Check size={14} className="shrink-0 text-accent" />}</div>
            {row.name && row.name !== row.id && <p className="break-all text-xs text-muted">{row.id}</p>}
            {describe(row) && <p className="mt-0.5 text-xs text-muted">{describe(row)}</p>}
            {row.discovered === false && <p className="text-xs text-muted">Configured ID · availability unverified</p>}
          </li>)}
        </ul>
        {!filtered.length && <p className="p-2 text-xs text-muted">{busy ? 'Discovering models…' : 'No matching chat models. Enter an exact model ID to add it manually.'}</p>}
      </div>}
    </div>
    <div className="mt-1.5 space-y-1 text-xs text-muted" role="status" aria-live="polite">
      {busy && <p>Refreshing model catalog…</p>}
      {(error || data?.error) && <p>{error || data.error} {data?.stale || (error && data) ? 'Showing the last available list.' : ''}</p>}
      {data?.mode === 'manual' && <p>Curated model list. An administrator can enable automatic discovery.</p>}
      {data?.last_success_at && <p>Last discovered {new Date(data.last_success_at).toLocaleTimeString()} · {data.source}</p>}
      {data?.limited && <p>The provider catalog was truncated. Enter an exact ID if your model is missing.</p>}
      {selected && <>
        {describe(selected) && <p>{describe(selected)}</p>}
        <p>Tools: {selected.supports_tools == null ? 'unknown' : selected.supports_tools ? 'reported' : 'not reported as supported'} · Vision: {selected.supports_vision == null ? 'unknown' : selected.supports_vision ? 'reported' : 'not reported as supported'}{selected.context_window ? ` · Max context: ${selected.context_window.toLocaleString()}` : ''}</p>
        {selected.loaded === false && <p>Enable Just-In-Time loading in LM Studio or load this model there before use.</p>}
      </>}
      {data?.source === 'bedrock' && <p>Listed models still require invocation access and Converse compatibility.</p>}
    </div>
  </div>
}
