import { useState } from 'react'
import { Menu, Settings, Cpu, History, FolderOpen, LogOut, ShieldCheck } from 'lucide-react'
import { useStore } from '../../store/useStore'
import CheckpointsModal from '../chat/CheckpointsModal'
import WorkspaceFilesModal from '../chat/WorkspaceFilesModal'
import AssistantAvatar from '../assistants/AssistantAvatar'

export default function Header({ onToggleSidebar, onOpenSettings }) {
  const settings = useStore((s) => s.settings)
  const providers = useStore((s) => s.providers)
  const activeId = useStore((s) => s.activeId)
  const user = useStore((s) => s.user)
  const authEnabled = useStore((s) => s.authConfig?.enabled)
  const logout = useStore((s) => s.logout)
  const assistants = useStore((s) => s.assistants)
  const activeAssistantId = useStore((s) => s.activeAssistantId)
  const activeProfile = providers.find((p) => p.name === settings?.active_profile)
  const assistant = assistants.find((a) => a.id === activeAssistantId) || null
  const [checkpointsOpen, setCheckpointsOpen] = useState(false)
  const [filesOpen, setFilesOpen] = useState(false)
  const [userMenu, setUserMenu] = useState(false)

  return (
    <header
      className="flex items-center justify-between border-b-4 px-4 py-2"
      style={{ background: 'var(--color-header)', borderColor: 'var(--color-header-border)' }}
    >
      <div className="flex items-center gap-3">
        <button
          onClick={onToggleSidebar}
          className="rounded p-2 hover:bg-surface-3 text-content"
          title="Toggle sidebar"
        >
          <Menu size={18} />
        </button>
        <img src="/phlox-logo.svg" alt="Phlox" className="h-8" />
        <span className="hidden sm:inline text-lg font-semibold text-content">Phlox</span>
      </div>
      <div className="flex items-center gap-2">
        {activeAssistantId && (
          <span
            className="flex items-center gap-2 rounded-full border border-border bg-surface px-3 py-1.5 text-sm text-content"
            title={assistant?.description || (assistant ? assistant.name : 'This assistant is no longer available; the chat keeps its saved settings.')}
          >
            {assistant ? (
              <>
                <AssistantAvatar assistant={assistant} size={18} />
                <span className="max-w-[140px] truncate">{assistant.name}</span>
              </>
            ) : (
              <span className="max-w-[180px] truncate text-muted">Assistant unavailable</span>
            )}
          </span>
        )}
        <button
          onClick={() => onOpenSettings('providers')}
          className="flex items-center gap-2 rounded-full border border-border bg-surface px-3 py-1.5 text-sm text-content hover:border-accent"
          title="Model & provider"
        >
          <Cpu size={15} className="text-accent" />
          <span className="max-w-[180px] truncate">
            {(assistant?.model) || settings?.model || activeProfile?.label || 'Configure model'}
          </span>
        </button>
        {activeId && (
          <>
            <button
              onClick={() => setFilesOpen(true)}
              className="rounded p-2 hover:bg-surface-3 text-content"
              title="Workspace files"
            >
              <FolderOpen size={18} />
            </button>
            <button
              onClick={() => setCheckpointsOpen(true)}
              className="rounded p-2 hover:bg-surface-3 text-content"
              title="Workspace checkpoints"
            >
              <History size={18} />
            </button>
          </>
        )}
        <button
          onClick={() => onOpenSettings('providers')}
          className="rounded p-2 hover:bg-surface-3 text-content"
          title="Settings"
        >
          <Settings size={18} />
        </button>
        {authEnabled && user && (
          <div className="relative">
            <button
              onClick={() => setUserMenu((v) => !v)}
              className="flex h-8 w-8 items-center justify-center rounded-full bg-accent text-sm font-semibold text-accent-fg"
              title={user.username}
            >
              {(user.display_name || user.username || '?').charAt(0).toUpperCase()}
            </button>
            {userMenu && (
              <>
                <div className="fixed inset-0 z-10" onClick={() => setUserMenu(false)} />
                <div className="absolute right-0 z-20 mt-2 w-48 rounded-lg border border-border bg-surface p-1 shadow-lg">
                  <div className="px-3 py-2 text-xs text-muted">
                    <div className="truncate font-medium text-content">{user.display_name || user.username}</div>
                    <div className="flex items-center gap-1">
                      {user.role === 'admin' && <ShieldCheck size={11} className="text-hutch-purple" />}
                      {user.role}
                    </div>
                  </div>
                  <button
                    onClick={() => { setUserMenu(false); logout() }}
                    className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-sm text-content hover:bg-surface-3"
                  >
                    <LogOut size={15} /> Sign out
                  </button>
                </div>
              </>
            )}
          </div>
        )}
      </div>
      {checkpointsOpen && activeId && (
        <CheckpointsModal conversationId={activeId} onClose={() => setCheckpointsOpen(false)} />
      )}
      {filesOpen && activeId && (
        <WorkspaceFilesModal conversationId={activeId} onClose={() => setFilesOpen(false)} />
      )}
    </header>
  )
}
