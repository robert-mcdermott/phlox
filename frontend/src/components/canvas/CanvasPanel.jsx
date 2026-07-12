import { useCallback, useEffect, useRef, useState } from 'react'
import { Code2, Download, ExternalLink, FileText, Loader2, RefreshCw, X, Eye } from 'lucide-react'
import { api } from '../../api/client'
import { useStore } from '../../store/useStore'
import Markdown from '../markdown/Markdown'

const MIN_WIDTH = 340
const MAX_WIDTH = 960
const DEFAULT_WIDTH = 480

const KIND_ICON = { html: Code2, markdown: FileText, code: FileText }
const KIND_LABEL = { html: 'HTML', markdown: 'Markdown', code: 'Text' }

// Drag handle on the left edge of the panel to resize it.
function useResize(width, setWidth) {
  const dragging = useRef(false)
  const onMouseDown = useCallback((e) => {
    e.preventDefault()
    dragging.current = true
    document.body.style.cursor = 'col-resize'
  }, [])
  useEffect(() => {
    const onMove = (e) => {
      if (!dragging.current) return
      const next = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, window.innerWidth - e.clientX))
      setWidth(next)
    }
    const onUp = () => {
      dragging.current = false
      document.body.style.cursor = ''
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
  }, [setWidth])
  return onMouseDown
}

export default function CanvasPanel() {
  const canvas = useStore((s) => s.canvas)
  const closeCanvas = useStore((s) => s.closeCanvas)
  const [width, setWidth] = useState(DEFAULT_WIDTH)
  const [text, setText] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [view, setView] = useState('preview') // 'preview' | 'source' — html only
  const onDragStart = useResize(width, setWidth)

  const load = useCallback(() => {
    if (!canvas) return
    setLoading(true)
    setError(null)
    api
      .getFileText(canvas.conversationId, canvas.path)
      .then(setText)
      .catch((e) => setError(String(e.message || e)))
      .finally(() => setLoading(false))
  }, [canvas])

  useEffect(() => {
    setView('preview')
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canvas?.conversationId, canvas?.path, canvas?.nonce])

  if (!canvas) return null

  const Icon = KIND_ICON[canvas.kind] || FileText
  const rawUrl = api.fileUrl(canvas.conversationId, canvas.path)

  return (
    <div
      className="relative flex h-full shrink-0 flex-col border-l border-border bg-surface"
      style={{ width }}
    >
      <div
        onMouseDown={onDragStart}
        className="absolute left-0 top-0 z-10 h-full w-1.5 -translate-x-1/2 cursor-col-resize hover:bg-accent/30"
        title="Drag to resize"
      />
      <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <Icon size={16} className="shrink-0 text-accent" />
          <span className="truncate text-sm font-medium text-content" title={canvas.name}>
            {canvas.name}
          </span>
          <span className="shrink-0 rounded bg-surface-3 px-1.5 py-0.5 text-[10px] text-muted">
            {KIND_LABEL[canvas.kind]}
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {(canvas.kind === 'html' || canvas.kind === 'markdown') && (
            <div className="mr-1 flex rounded-md border border-border p-0.5 text-xs">
              <button
                onClick={() => setView('preview')}
                className={`flex items-center gap-1 rounded px-2 py-1 ${view === 'preview' ? 'bg-accent text-accent-fg' : 'text-muted hover:text-content'}`}
              >
                <Eye size={12} /> Preview
              </button>
              <button
                onClick={() => setView('source')}
                className={`flex items-center gap-1 rounded px-2 py-1 ${view === 'source' ? 'bg-accent text-accent-fg' : 'text-muted hover:text-content'}`}
              >
                <Code2 size={12} /> Source
              </button>
            </div>
          )}
          <button onClick={load} className="rounded p-1.5 text-muted hover:bg-surface-3 hover:text-content" title="Refresh">
            <RefreshCw size={15} />
          </button>
          <button
            type="button"
            onClick={() => api.openFile(rawUrl)}
            className="rounded p-1.5 text-muted hover:bg-surface-3 hover:text-content"
            title="Open raw file in a new tab"
          >
            <ExternalLink size={15} />
          </button>
          <button
            type="button"
            onClick={() => api.downloadFile(rawUrl, canvas.name)}
            className="rounded p-1.5 text-muted hover:bg-surface-3 hover:text-content"
            title="Download"
          >
            <Download size={15} />
          </button>
          <button onClick={closeCanvas} className="rounded p-1.5 text-muted hover:bg-surface-3 hover:text-content" title="Close">
            <X size={16} />
          </button>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-hidden">
        {loading ? (
          <div className="flex h-full items-center justify-center">
            <Loader2 className="animate-spin text-accent" />
          </div>
        ) : error ? (
          <div className="p-4 text-sm text-red-600">Failed to load: {error}</div>
        ) : canvas.kind === 'html' && view === 'preview' ? (
          <iframe
            key={canvas.nonce}
            srcDoc={text}
            title={canvas.name}
            sandbox="allow-scripts allow-forms allow-popups allow-modals"
            className="h-full w-full border-0 bg-white"
          />
        ) : canvas.kind === 'markdown' && view === 'preview' ? (
          <div className="h-full overflow-y-auto p-4">
            <Markdown>{text}</Markdown>
          </div>
        ) : (
          <pre className="h-full overflow-auto whitespace-pre-wrap break-words p-4 font-mono text-xs text-content">
            {text}
          </pre>
        )}
      </div>
    </div>
  )
}
