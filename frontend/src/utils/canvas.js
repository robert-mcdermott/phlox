// Classifies a workspace artifact by extension so the UI knows whether/how to render it
// in the artifact canvas (side panel preview) instead of just offering a download.
const HTML_EXTS = ['.html', '.htm']
const MARKDOWN_EXTS = ['.md', '.markdown']
const IMAGE_EXTS = ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.bmp']
// Binary/opaque formats: no sane inline text preview, so fall back to download-only.
const NON_TEXT_EXTS = [
  ...IMAGE_EXTS,
  '.pdf', '.zip', '.tar', '.gz', '.7z', '.rar', '.exe', '.bin',
  '.sqlite', '.db', '.ico', '.woff', '.woff2', '.ttf', '.eot',
  '.mp3', '.mp4', '.mov', '.wav', '.ogg', '.docx', '.xlsx', '.pptx',
]

// Returns 'html' | 'markdown' | 'code' (generic text preview) | null (not canvas-able).
export function canvasKind(ext) {
  const e = (ext || '').toLowerCase()
  if (HTML_EXTS.includes(e)) return 'html'
  if (MARKDOWN_EXTS.includes(e)) return 'markdown'
  if (NON_TEXT_EXTS.includes(e)) return null
  return 'code'
}
