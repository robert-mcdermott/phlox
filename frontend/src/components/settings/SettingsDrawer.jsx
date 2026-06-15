import { useState } from 'react'
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

export default function SettingsDrawer({ initialTab = 'providers', onClose }) {
  const isAdmin = useStore((s) => s.user?.role === 'admin')
  const [tab, setTab] = useState(initialTab)

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
      <div className="flex h-full w-full max-w-2xl flex-col bg-bg shadow-2xl" onClick={(e) => e.stopPropagation()}>
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
