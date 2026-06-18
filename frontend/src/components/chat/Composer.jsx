import { useRef, useState } from 'react'
import { Send, Square, Paperclip, Zap, ImagePlus, X, Search } from 'lucide-react'
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

export default function Composer() {
  const [text, setText] = useState('')
  const [autoApprove, setAutoApprove] = useState(true)
  const [webSearch, setWebSearch] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [images, setImages] = useState([]) // data URLs
  const streaming = useStore((s) => s.streaming)
  const send = useStore((s) => s.sendMessage)
  const stop = useStore((s) => s.stopStreaming)
  const activeId = useStore((s) => s.activeId)
  const queued = useStore((s) => s.queued)
  const clearQueued = useStore((s) => s.clearQueued)
  const taRef = useRef(null)
  const fileRef = useRef(null)
  const imgRef = useRef(null)

  const submit = () => {
    if (!text.trim() && images.length === 0) return
    // While streaming, the store queues this as a follow-up (steering).
    send(text.trim(), { autoApprove, webSearch, images })
    setText('')
    setImages([])
    if (taRef.current) taRef.current.style.height = 'auto'
  }

  const onKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      submit()
    }
  }

  const grow = (e) => {
    setText(e.target.value)
    const el = e.target
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 220) + 'px'
  }

  const onUpload = async (e) => {
    const file = e.target.files?.[0]
    if (!file) return
    setUploading(true)
    try {
      // Scope to the current conversation if one is active; otherwise global KB.
      await api.uploadDocument(file, activeId)
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

  return (
    <div className="border-t border-border bg-surface px-4 py-3">
      <div className="mx-auto max-w-3xl">
        {queued && (
          <div className="mb-2 flex items-center gap-2 rounded-lg border border-accent/40 bg-accent/10 px-3 py-1.5 text-xs text-content">
            <span className="font-medium text-accent">Queued follow-up:</span>
            <span className="flex-1 truncate">{queued.text || '(image)'}</span>
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
        <div className="flex items-end gap-2 rounded-2xl border border-border bg-surface-2 px-3 py-2 focus-within:border-accent">
          <input ref={fileRef} type="file" className="hidden" onChange={onUpload} />
          <input ref={imgRef} type="file" accept="image/*" multiple className="hidden" onChange={onAddImages} />
          <button
            onClick={() => fileRef.current?.click()}
            disabled={uploading}
            title="Upload a document to the knowledge base"
            className="mb-1 rounded-md p-1.5 text-muted hover:text-accent disabled:opacity-50"
          >
            <Paperclip size={18} />
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
            rows={1}
            placeholder={
              uploading ? 'Uploading document…' : streaming ? 'Queue a follow-up…' : 'Message Phlox…'
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
              disabled={!text.trim() && images.length === 0}
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
              <Zap size={12} /> Agent mode (auto-run tools)
            </label>
            <label className="flex cursor-pointer items-center gap-1.5" title="Allow live web search for this prompt">
              <input type="checkbox" checked={webSearch} onChange={(e) => setWebSearch(e.target.checked)}
                className="rounded border-border text-accent focus:ring-accent" />
              <Search size={12} /> Web search
            </label>
          </div>
          <TokenMeter />
        </div>
      </div>
    </div>
  )
}
