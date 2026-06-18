import { useCallback, useEffect, useRef, useState } from 'react'
import { X, Cpu, Palette, FileText, Server, Wrench, Brain, Users, ShieldCheck, KeyRound, BarChart3, SlidersHorizontal } from 'lucide-react'
import ProviderSettings from './ProviderSettings'
import ThemeSwitcher from './ThemeSwitcher'
import MemoryPanel from './MemoryPanel'
import UsersPanel from './UsersPanel'
import UsagePanel from './UsagePanel'
import ConfigPanel from './ConfigPanel'
import AuthSettingsPanel from './AuthSettingsPanel'
import DocumentsPanel from '../documents/DocumentsPanel'
import McpManager from '../mcp/McpManager'
import ToolManager from '../tools/ToolManager'
import { useStore } from '../../store/useStore'

// User-level settings (everyone) and admin-only settings (role === 'admin').
const USER_TABS = [
  { id: 'providers', label: 'Model', icon: Cpu },
  { id: 'appearance', label: 'Appearance', icon: Palette },
  { id: 'documents', label: 'Documents', icon: FileText },
  { id: 'memory', label: 'Memory', icon: Brain },
]
const ADMIN_TABS = [
  { id: 'users', label: 'Users', icon: Users },
  { id: 'usage', label: 'Usage & Cost', icon: BarChart3 },
  { id: 'config', label: 'Configuration', icon: SlidersHorizontal },
  { id: 'auth', label: 'Authentication', icon: KeyRound },
  { id: 'mcp', label: 'MCP Servers', icon: Server },
  { id: 'tools', label: 'Tools', icon: Wrench },
]

// Width of the sliding settings drawer, in pixels. The drawer is wider by default
// than the old fixed `max-w-2xl` (672px) and can be resized by dragging its left
// edge; the chosen width is remembered across sessions.
const WIDTH_STORAGE_KEY = 'phlox.settingsDrawerWidth'
const DEFAULT_WIDTH = 820
const MIN_WIDTH = 560
const RESIZE_STEP = 32 // px moved per arrow-key press when the handle is focused

// Keep the drawer usable on small viewports: never wider than the window (minus a
// small gutter) and never below the minimum, with the minimum itself capped so the
// drawer still fits on narrow screens.
function clampWidth(width, viewport = window.innerWidth) {
  const maxWidth = Math.max(MIN_WIDTH, viewport - 48)
  const minWidth = Math.min(MIN_WIDTH, maxWidth)
  return Math.min(Math.max(width, minWidth), maxWidth)
}

function loadInitialWidth() {
  if (typeof window === 'undefined') return DEFAULT_WIDTH
  const stored = Number.parseInt(window.localStorage.getItem(WIDTH_STORAGE_KEY), 10)
  return clampWidth(Number.isFinite(stored) ? stored : DEFAULT_WIDTH)
}

export default function SettingsDrawer({ initialTab = 'providers', onClose }) {
  const isAdmin = useStore((s) => s.user?.role === 'admin')
  const [tab, setTab] = useState(initialTab)
  const [width, setWidth] = useState(loadInitialWidth)
  const [resizing, setResizing] = useState(false)
  const widthRef = useRef(width)
  widthRef.current = width

  // Persist the chosen width whenever it settles.
  useEffect(() => {
    try {
      window.localStorage.setItem(WIDTH_STORAGE_KEY, String(width))
    } catch {
      // Ignore storage failures (e.g. private mode); width still works for the session.
    }
  }, [width])

  // Re-clamp if the window shrinks below the current drawer width.
  useEffect(() => {
    const onResize = () => setWidth((w) => clampWidth(w))
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  // Drag-to-resize from the left edge. The drawer is anchored to the right, so a
  // smaller distance from the right edge means a wider panel.
  const startResize = useCallback((e) => {
    e.preventDefault()
    setResizing(true)
    const onMove = (ev) => {
      const clientX = ev.touches ? ev.touches[0].clientX : ev.clientX
      setWidth(clampWidth(window.innerWidth - clientX))
    }
    const onUp = () => {
      setResizing(false)
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
      window.removeEventListener('touchmove', onMove)
      window.removeEventListener('touchend', onUp)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    window.addEventListener('touchmove', onMove, { passive: false })
    window.addEventListener('touchend', onUp)
  }, [])

  // Keyboard resize when the handle is focused; double-click resets to default.
  const onHandleKeyDown = useCallback((e) => {
    if (e.key === 'ArrowLeft') {
      e.preventDefault()
      setWidth((w) => clampWidth(w + RESIZE_STEP))
    } else if (e.key === 'ArrowRight') {
      e.preventDefault()
      setWidth((w) => clampWidth(w - RESIZE_STEP))
    } else if (e.key === 'Home') {
      e.preventDefault()
      setWidth(clampWidth(DEFAULT_WIDTH))
    }
  }, [])

  const TabButton = ({ t }) => {
    const Icon = t.icon
    return (
      <button
        onClick={() => setTab(t.id)}
        className={`mb-0.5 flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm ${
          tab === t.id ? 'bg-surface-3 font-medium text-content' : 'text-muted hover:bg-surface-2'
        }`}
      >
        <Icon size={15} /> {t.label}
      </button>
    )
  }

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/40" onClick={onClose}>
      <div
        className="relative flex h-full w-full flex-col bg-bg shadow-2xl"
        style={{ maxWidth: `${width}px` }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Resize handle on the left edge of the drawer. */}
        <div
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize settings panel"
          aria-valuenow={Math.round(width)}
          aria-valuemin={MIN_WIDTH}
          aria-valuemax={Math.round(clampWidth(window.innerWidth))}
          tabIndex={0}
          title="Drag to resize · double-click to reset"
          onMouseDown={startResize}
          onTouchStart={startResize}
          onDoubleClick={() => setWidth(clampWidth(DEFAULT_WIDTH))}
          onKeyDown={onHandleKeyDown}
          className="group absolute left-0 top-0 z-10 flex h-full w-2 -translate-x-1/2 cursor-col-resize touch-none items-center justify-center outline-none"
        >
          <span
            className={`h-full w-0.5 transition-colors ${
              resizing ? 'bg-accent' : 'bg-transparent group-hover:bg-accent/60 group-focus-visible:bg-accent'
            }`}
          />
        </div>

        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <h2 className="text-lg font-semibold text-content">Settings</h2>
          <button onClick={onClose} className="rounded p-2 text-muted hover:bg-surface-3 hover:text-content">
            <X size={18} />
          </button>
        </div>

        <div className="flex flex-1 min-h-0">
          <nav className="w-44 shrink-0 overflow-y-auto border-r border-border p-2">
            {USER_TABS.map((t) => <TabButton key={t.id} t={t} />)}
            {isAdmin && (
              <>
                <div className="mt-3 mb-1 flex items-center gap-1.5 px-3 text-[10px] font-semibold uppercase tracking-wide text-muted">
                  <ShieldCheck size={12} /> Admin
                </div>
                {ADMIN_TABS.map((t) => <TabButton key={t.id} t={t} />)}
              </>
            )}
          </nav>

          <div className="flex-1 overflow-y-auto p-5">
            {tab === 'providers' && <ProviderSettings />}
            {tab === 'appearance' && <ThemeSwitcher />}
            {tab === 'documents' && <DocumentsPanel />}
            {tab === 'memory' && <MemoryPanel />}
            {isAdmin && tab === 'mcp' && <McpManager />}
            {isAdmin && tab === 'tools' && <ToolManager />}
            {isAdmin && tab === 'users' && <UsersPanel />}
            {isAdmin && tab === 'usage' && <UsagePanel />}
            {isAdmin && tab === 'config' && <ConfigPanel />}
            {isAdmin && tab === 'auth' && <AuthSettingsPanel />}
          </div>
        </div>
      </div>
    </div>
  )
}
