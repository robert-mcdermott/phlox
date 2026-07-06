import { useState } from 'react'
import { Download, Eye, FileText, Image as ImageIcon, X } from 'lucide-react'
import { canvasKind } from '../../utils/canvas'
import { useStore } from '../../store/useStore'

const IMAGE_EXTS = ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg']

function Lightbox({ url, name, onClose }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-6" onClick={onClose}>
      <button className="absolute right-4 top-4 rounded-full bg-white/10 p-2 text-white hover:bg-white/20" onClick={onClose}>
        <X size={20} />
      </button>
      <img src={url} alt={name} className="max-h-full max-w-full rounded-lg object-contain" onClick={(e) => e.stopPropagation()} />
    </div>
  )
}

export default function ArtifactViewer({ artifacts, conversationId }) {
  const [lightbox, setLightbox] = useState(null)
  const openCanvasArtifact = useStore((s) => s.openCanvasArtifact)
  if (!artifacts || artifacts.length === 0) return null
  return (
    <div className="mt-2 flex flex-wrap gap-3">
      {artifacts.map((a, i) => {
        const url = a.url || `/api/files/${conversationId}?path=${encodeURIComponent(a.path)}`
        const isImage = IMAGE_EXTS.includes((a.ext || '').toLowerCase())
        const viewable = !isImage && canvasKind(a.ext)
        return (
          <div key={i} className="overflow-hidden rounded-lg border border-border bg-surface-2">
            {isImage ? (
              <button onClick={() => setLightbox({ url, name: a.name })} title="Click to enlarge">
                <img src={url} alt={a.name} className="max-h-72 max-w-xs cursor-zoom-in object-contain" />
              </button>
            ) : viewable ? (
              <button
                onClick={() => openCanvasArtifact(a, conversationId)}
                className="flex items-center gap-2 px-3 py-4 hover:bg-surface-3"
                title="Open in canvas"
              >
                <Eye size={20} className="text-accent" />
                <span className="text-sm text-content">{a.name}</span>
              </button>
            ) : (
              <div className="flex items-center gap-2 px-3 py-4">
                <FileText size={20} className="text-accent" />
                <span className="text-sm text-content">{a.name}</span>
              </div>
            )}
            <a
              href={url}
              download={a.name}
              className="flex items-center gap-1 border-t border-border px-2 py-1 text-xs text-muted hover:text-accent"
            >
              {isImage ? <ImageIcon size={12} /> : <Download size={12} />} {a.name}
            </a>
          </div>
        )
      })}
      {lightbox && <Lightbox {...lightbox} onClose={() => setLightbox(null)} />}
    </div>
  )
}
