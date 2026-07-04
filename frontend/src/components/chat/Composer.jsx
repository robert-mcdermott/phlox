import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Send,
  Square,
  Paperclip,
  Zap,
  ImagePlus,
  X,
  Search,
  FileSearch,
  FileText,
  Loader2,
  AlertCircle,
} from 'lucide-react'
import { useStore } from '../../store/useStore'
import { api } from '../../api/client'
import TokenMeter from './TokenMeter'

function fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result)
    reader.onerror = reject
    reader.readAsDataURL(file)
  })
}

function scopedDocuments(docs, activeId) {
  return docs.filter((d) => !d.conversation_id || d.conversation_id === activeId)
}

function docStatus(doc) {
  if (doc.status === 'ready') return `${doc.n_chunks || 0} chunks`
  if (doc.status === 'error') return doc.error || 'Indexing failed'
  return 'Indexing...'
}

export default function Composer() {
  const [text, setText] = useState('')
  const [autoApprove, setAutoApprove] = useState(true)
  const [webSearch, setWebSearch] = useState(false)
  const [documentSearch, setDocumentSearch] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [images, setImages] = useState([]) // data URLs
  const [documents, setDocuments] = useState([])
  const [documentRefs, setDocumentRefs] = useState([])
  const [docPicker, setDocPicker] = useState({ open: false, query: '', start: 0, end: 0 })
  const [activeDocIndex, setActiveDocIndex] = useState(0)
  const streaming = useStore((s) => s.streaming)
  const send = useStore((s) => s.sendMessage)
  const stop = useStore((s) => s.stopStreaming)
  const activeId = useStore((s) => s.activeId)
  const queued = useStore((s) => s.queued)
  const clearQueued = useStore((s) => s.clearQueued)
  const taRef = useRef(null)
  const fileRef = useRef(null)
  const imgRef = useRef(null)

  const loadDocuments = useCallback(async () => {
    try {
      const rows = scopedDocuments(await api.listDocuments(activeId), activeId)
      setDocuments(rows)
      setDocumentRefs((prev) =>
        prev
          .map((ref) => rows.find((d) => d.id === (ref.id || ref.document_id)) || ref)
          .filter((ref) => !ref.conversation_id || ref.conversation_id === activeId),
      )
    } catch {
      setDocuments([])
    }
  }, [activeId])

  useEffect(() => {
    setDocumentRefs([])
    setDocPicker({ open: false, query: '', start: 0, end: 0 })
    loadDocuments()
  }, [activeId, loadDocuments])

  useEffect(() => {
    if (!documentRefs.some((d) => d.status && d.status !== 'ready')) return undefined
    const timer = setInterval(loadDocuments, 1500)
    return () => clearInterval(timer)
  }, [documentRefs, loadDocuments])

  useEffect(() => {
    setActiveDocIndex(0)
  }, [docPicker.open, docPicker.query])

  const pickerDocs = useMemo(() => {
    if (!docPicker.open) return []
    const q = docPicker.query.toLowerCase()
    const selected = new Set(documentRefs.map((d) => d.id || d.document_id))
    return documents
      .filter((d) => d.status === 'ready')
      .filter((d) => !selected.has(d.id))
      .filter((d) => !q || d.filename.toLowerCase().includes(q))
      .slice(0, 7)
  }, [docPicker.open, docPicker.query, documentRefs, documents])

  const pendingDocs = documentRefs.filter((d) => d.status && d.status !== 'ready')
  const erroredDocs = documentRefs.filter((d) => d.status === 'error')
  const documentIds = documentRefs.map((d) => d.id || d.document_id).filter(Boolean)
  const canSubmit =
    (text.trim() || images.length > 0 || documentIds.length > 0) &&
    !uploading &&
    pendingDocs.length === 0 &&
    erroredDocs.length === 0

  const updateMentionPicker = (value, cursor) => {
    const before = value.slice(0, cursor)
    const match = /(^|\s)@([^\s@]*)$/.exec(before)
    if (!match) {
      setDocPicker((prev) => (prev.open ? { open: false, query: '', start: 0, end: 0 } : prev))
      return
    }
    const query = match[2] || ''
    setDocPicker({ open: true, query, start: cursor - query.length - 1, end: cursor })
    loadDocuments()
  }

  const setTextareaHeight = () => {
    const el = taRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 220) + 'px'
  }

  const addDocumentRef = (doc) => {
    setDocumentRefs((prev) =>
      prev.some((d) => (d.id || d.document_id) === doc.id) ? prev : [...prev, doc],
    )
  }

  const pickDocument = (doc) => {
    addDocumentRef(doc)
    const mention = `@${doc.filename} `
    const start = docPicker.start
    const end = docPicker.end
    setText((prev) => prev.slice(0, start) + mention + prev.slice(end))
    setDocPicker({ open: false, query: '', start: 0, end: 0 })
    window.setTimeout(() => {
      const pos = start + mention.length
      taRef.current?.focus()
      taRef.current?.setSelectionRange(pos, pos)
      setTextareaHeight()
    }, 0)
  }

  const submit = () => {
    if (!canSubmit) return
    // While streaming, the store queues this as a follow-up (steering).
    send(text.trim(), {
      autoApprove,
      webSearch,
      documentSearch,
      images,
      documentIds,
      documentRefs,
    })
    setText('')
    setImages([])
    setDocumentRefs([])
    setDocPicker({ open: false, query: '', start: 0, end: 0 })
    if (taRef.current) taRef.current.style.height = 'auto'
  }

  const onKeyDown = (e) => {
    if (docPicker.open) {
      if (e.key === 'Escape') {
        e.preventDefault()
        setDocPicker({ open: false, query: '', start: 0, end: 0 })
        return
      }
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setActiveDocIndex((i) => Math.min(i + 1, Math.max(pickerDocs.length - 1, 0)))
        return
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault()
        setActiveDocIndex((i) => Math.max(i - 1, 0))
        return
      }
      if ((e.key === 'Enter' || e.key === 'Tab') && pickerDocs.length > 0) {
        e.preventDefault()
        pickDocument(pickerDocs[activeDocIndex] || pickerDocs[0])
        return
      }
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      submit()
    }
  }

  const grow = (e) => {
    const value = e.target.value
    setText(value)
    const el = e.target
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 220) + 'px'
    updateMentionPicker(value, el.selectionStart || value.length)
  }

  const onUpload = async (e) => {
    const file = e.target.files?.[0]
    if (!file) return
    setUploading(true)
    try {
      // Scope to the current conversation if one is active; otherwise upload as global KB.
      const doc = await api.uploadDocument(file, activeId)
      addDocumentRef(doc)
      await loadDocuments()
    } finally {
      setUploading(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const onAddImages = async (e) => {
    const files = Array.from(e.target.files || [])
    const urls = await Promise.all(files.map(fileToDataUrl))
    setImages((prev) => [...prev, ...urls])
    if (imgRef.current) imgRef.current.value = ''
  }

  const queuedLabel = queued
    ? queued.text ||
      (queued.documentRefs?.length
        ? `${queued.documentRefs.length} document${queued.documentRefs.length === 1 ? '' : 's'}`
        : '(image)')
    : ''

  return (
    <div className="border-t border-border bg-surface px-4 py-3">
      <div className="mx-auto max-w-3xl">
        {queued && (
          <div className="mb-2 flex items-center gap-2 rounded-lg border border-accent/40 bg-accent/10 px-3 py-1.5 text-xs text-content">
            <span className="font-medium text-accent">Queued follow-up:</span>
            <span className="flex-1 truncate">{queuedLabel}</span>
            <button onClick={clearQueued} className="text-muted hover:text-red-600" title="Cancel">
              <X size={13} />
            </button>
          </div>
        )}
        {images.length > 0 && (
          <div className="mb-2 flex flex-wrap gap-2">
            {images.map((url, i) => (
              <div key={i} className="relative">
                <img src={url} alt="" className="h-16 w-16 rounded-lg border border-border object-cover" />
                <button
                  onClick={() => setImages((prev) => prev.filter((_, j) => j !== i))}
                  className="absolute -right-1.5 -top-1.5 rounded-full bg-black/70 p-0.5 text-white hover:bg-black"
                  title="Remove"
                >
                  <X size={12} />
                </button>
              </div>
            ))}
          </div>
        )}
        {documentRefs.length > 0 && (
          <div className="mb-2 flex flex-wrap gap-2">
            {documentRefs.map((doc) => {
              const ready = doc.status === 'ready'
              const error = doc.status === 'error'
              return (
                <div
                  key={doc.id || doc.document_id}
                  className={`flex max-w-full items-center gap-2 rounded-lg border px-2.5 py-1.5 text-xs ${
                    error
                      ? 'border-red-300 bg-red-50 text-red-700'
                      : 'border-border bg-surface-2 text-content'
                  }`}
                  title={doc.filename}
                >
                  {ready ? (
                    <FileText size={14} className="shrink-0 text-accent" />
                  ) : error ? (
                    <AlertCircle size={14} className="shrink-0" />
                  ) : (
                    <Loader2 size={14} className="shrink-0 animate-spin text-accent" />
                  )}
                  <span className="max-w-[16rem] truncate">{doc.filename}</span>
                  <span className="shrink-0 text-muted">{docStatus(doc)}</span>
                  <button
                    onClick={() =>
                      setDocumentRefs((prev) =>
                        prev.filter((d) => (d.id || d.document_id) !== (doc.id || doc.document_id)),
                      )
                    }
                    className="rounded p-0.5 text-muted hover:text-red-600"
                    title="Remove"
                  >
                    <X size={12} />
                  </button>
                </div>
              )
            })}
          </div>
        )}
        {docPicker.open && (
          <div className="mb-2 max-h-56 overflow-y-auto rounded-lg border border-border bg-surface shadow-lg">
            {pickerDocs.length === 0 ? (
              <div className="px-3 py-2 text-sm text-muted">No matching ready documents</div>
            ) : (
              pickerDocs.map((doc, i) => (
                <button
                  key={doc.id}
                  type="button"
                  onMouseDown={(e) => {
                    e.preventDefault()
                    pickDocument(doc)
                  }}
                  className={`flex w-full items-center gap-2 px-3 py-2 text-left text-sm ${
                    i === activeDocIndex ? 'bg-surface-2 text-content' : 'text-content hover:bg-surface-2'
                  }`}
                  title={doc.filename}
                >
                  <FileText size={15} className="shrink-0 text-accent" />
                  <span className="min-w-0 flex-1 truncate">{doc.filename}</span>
                  <span className="shrink-0 text-xs text-muted">{docStatus(doc)}</span>
                </button>
              ))
            )}
          </div>
        )}
        <div className="flex items-end gap-2 rounded-2xl border border-border bg-surface-2 px-3 py-2 focus-within:border-accent">
          <input
            ref={fileRef}
            type="file"
            className="hidden"
            onChange={onUpload}
            accept=".pdf,.docx,.txt,.md,.markdown,.py,.js,.ts,.json,.csv,.html,.xml,.yaml,.yml"
          />
          <input ref={imgRef} type="file" accept="image/*" multiple className="hidden" onChange={onAddImages} />
          <button
            onClick={() => fileRef.current?.click()}
            disabled={uploading}
            title="Attach a document to this message"
            className="mb-1 rounded-md p-1.5 text-muted hover:text-accent disabled:opacity-50"
          >
            {uploading ? <Loader2 size={18} className="animate-spin" /> : <Paperclip size={18} />}
          </button>
          <button
            onClick={() => imgRef.current?.click()}
            title="Attach image(s) for a vision model"
            className="mb-1 rounded-md p-1.5 text-muted hover:text-accent"
          >
            <ImagePlus size={18} />
          </button>
          <textarea
            ref={taRef}
            value={text}
            onChange={grow}
            onKeyDown={onKeyDown}
            onClick={(e) => updateMentionPicker(e.currentTarget.value, e.currentTarget.selectionStart || 0)}
            rows={1}
            placeholder={
              uploading
                ? 'Uploading document…'
                : pendingDocs.length
                  ? 'Indexing referenced document…'
                  : streaming
                    ? 'Queue a follow-up…'
                    : 'Message Phlox…'
            }
            className="max-h-[220px] flex-1 resize-none bg-transparent py-1.5 text-content outline-none placeholder:text-muted"
          />
          {streaming ? (
            <button onClick={stop} className="mb-0.5 rounded-lg bg-red-500 p-2 text-white hover:bg-red-600" title="Stop">
              <Square size={16} />
            </button>
          ) : (
            <button
              onClick={submit}
              disabled={!canSubmit}
              className="mb-0.5 rounded-lg bg-accent p-2 text-accent-fg hover:opacity-90 disabled:opacity-40"
              title="Send"
            >
              <Send size={16} />
            </button>
          )}
        </div>
        <div className="mt-1.5 flex items-center justify-between gap-3 px-1 text-xs text-muted">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
            <label className="flex cursor-pointer items-center gap-1.5" title="Auto-approve tools that normally ask">
              <input type="checkbox" checked={autoApprove} onChange={(e) => setAutoApprove(e.target.checked)}
                className="rounded border-border text-accent focus:ring-accent" />
              <Zap size={12} /> Agent mode
            </label>
            <label className="flex cursor-pointer items-center gap-1.5" title="Allow live web search for this prompt">
              <input type="checkbox" checked={webSearch} onChange={(e) => setWebSearch(e.target.checked)}
                className="rounded border-border text-accent focus:ring-accent" />
              <Search size={12} /> Web search
            </label>
            <label className="flex cursor-pointer items-center gap-1.5" title="Search uploaded documents for this prompt">
              <input type="checkbox" checked={documentSearch} onChange={(e) => setDocumentSearch(e.target.checked)}
                className="rounded border-border text-accent focus:ring-accent" />
              <FileSearch size={12} /> Search documents
            </label>
          </div>
          <TokenMeter />
        </div>
      </div>
    </div>
  )
}
