import { useState } from 'react'
import { Plus, MessageSquare, Trash2, Pencil, FileText, Server, Wrench, Palette, Search, Download } from 'lucide-react'
import { useStore } from '../../store/useStore'

function IconBtn({ title, onClick, children }) {
  return (
    <button
      onClick={onClick}
      title={title}
      className="rounded p-1.5 hover:bg-white/10"
      style={{ color: 'var(--color-sidebar-fg)' }}
    >
      {children}
    </button>
  )
}

export default function Sidebar({ onOpenSettings }) {
  const conversations = useStore((s) => s.conversations)
  const activeId = useStore((s) => s.activeId)
  const select = useStore((s) => s.selectConversation)
  const newConv = useStore((s) => s.newConversation)
  const del = useStore((s) => s.deleteConversation)
  const rename = useStore((s) => s.renameConversation)
  const exportConv = useStore((s) => s.exportConversation)
  const isAdmin = useStore((s) => s.user?.role === 'admin')
  const [editing, setEditing] = useState(null)
  const [draft, setDraft] = useState('')
  const [query, setQuery] = useState('')

  const submitRename = (id) => {
    if (draft.trim()) rename(id, draft.trim())
    setEditing(null)
  }

  const filtered = query.trim()
    ? conversations.filter((c) => c.title.toLowerCase().includes(query.trim().toLowerCase()))
    : conversations

  return (
    <aside
      className="flex w-64 flex-col"
      style={{ background: 'var(--color-sidebar)', color: 'var(--color-sidebar-fg)' }}
    >
      <div className="p-3">
        <button
          onClick={newConv}
          className="flex w-full items-center justify-center gap-2 rounded-lg bg-hutch-cyan px-3 py-2 text-sm font-medium text-white hover:opacity-90"
        >
          <Plus size={16} /> New chat
        </button>
        <div className="relative mt-2">
          <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 opacity-50" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search chats…"
            className="w-full rounded-lg border-0 bg-black/20 py-1.5 pl-8 pr-2 text-sm placeholder:opacity-50 focus:ring-1 focus:ring-white/30"
            style={{ color: 'var(--color-sidebar-fg)' }}
          />
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-2">
        {filtered.length === 0 && (
          <p className="px-2 py-4 text-xs opacity-60">{query ? 'No matches.' : 'No conversations yet.'}</p>
        )}
        {filtered.map((c) => (
          <div
            key={c.id}
            className={`group mb-0.5 flex items-center gap-2 rounded-lg px-2 py-2 text-sm cursor-pointer ${
              c.id === activeId ? 'bg-white/15' : 'hover:bg-white/10'
            }`}
            onClick={() => select(c.id)}
          >
            <MessageSquare size={15} className="shrink-0 opacity-70" />
            {editing === c.id ? (
              <input
                autoFocus
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onBlur={() => submitRename(c.id)}
                onKeyDown={(e) => e.key === 'Enter' && submitRename(c.id)}
                onClick={(e) => e.stopPropagation()}
                className="flex-1 rounded bg-black/30 px-1 text-sm text-white outline-none"
              />
            ) : (
              <span className="flex-1 truncate">{c.title}</span>
            )}
            <div className="hidden shrink-0 gap-0.5 group-hover:flex">
              <IconBtn
                title="Rename"
                onClick={(e) => {
                  e.stopPropagation()
                  setEditing(c.id)
                  setDraft(c.title)
                }}
              >
                <Pencil size={13} />
              </IconBtn>
              <IconBtn
                title="Export as Markdown"
                onClick={(e) => {
                  e.stopPropagation()
                  exportConv(c.id)
                }}
              >
                <Download size={13} />
              </IconBtn>
              <IconBtn
                title="Delete"
                onClick={(e) => {
                  e.stopPropagation()
                  if (confirm('Delete this conversation?')) del(c.id)
                }}
              >
                <Trash2 size={13} />
              </IconBtn>
            </div>
          </div>
        ))}
      </div>

      <div className="border-t border-white/10 p-2 space-y-0.5">
        <SidebarLink icon={<FileText size={15} />} label="Documents" onClick={() => onOpenSettings('documents')} />
        {isAdmin && (
          <>
            <SidebarLink icon={<Server size={15} />} label="MCP Servers" onClick={() => onOpenSettings('mcp')} />
            <SidebarLink icon={<Wrench size={15} />} label="Tools" onClick={() => onOpenSettings('tools')} />
          </>
        )}
        <SidebarLink icon={<Palette size={15} />} label="Appearance" onClick={() => onOpenSettings('appearance')} />
      </div>
    </aside>
  )
}

function SidebarLink({ icon, label, onClick }) {
  return (
    <button
      onClick={onClick}
      className="flex w-full items-center gap-2 rounded-lg px-2 py-2 text-sm hover:bg-white/10"
      style={{ color: 'var(--color-sidebar-fg)' }}
    >
      {icon} {label}
    </button>
  )
}
