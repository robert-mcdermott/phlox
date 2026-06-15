import { useEffect, useState } from 'react'
import { Brain, Trash2, Plus } from 'lucide-react'
import { api } from '../../api/client'

const KIND_COLORS = {
  fact: 'bg-hutch-cyan/15 text-hutch-cyan',
  preference: 'bg-hutch-purple/15 text-hutch-purple',
  project: 'bg-hutch-gold/20 text-hutch-gold',
}

export default function MemoryPanel() {
  const [memories, setMemories] = useState([])
  const [content, setContent] = useState('')
  const [kind, setKind] = useState('fact')

  const load = () => api.listMemories().then(setMemories).catch(() => setMemories([]))
  useEffect(() => { load() }, [])

  const add = async () => {
    if (!content.trim()) return
    await api.addMemory({ content: content.trim(), kind })
    setContent('')
    load()
  }

  return (
    <div>
      <h3 className="mb-1 text-sm font-semibold text-content">Long-term memory</h3>
      <p className="mb-4 text-xs text-muted">
        Durable facts the assistant recalls across conversations. The assistant can save
        these itself (<code className="rounded bg-surface-3 px-1">save_memory</code>), and
        relevant ones are injected into context automatically.
      </p>

      <div className="mb-4 flex gap-2">
        <input
          value={content}
          onChange={(e) => setContent(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && add()}
          placeholder="Add a memory (e.g. 'Prefers Python over R')"
          className="flex-1 rounded-lg border-border bg-surface text-sm text-content focus:border-accent focus:ring-accent"
        />
        <select value={kind} onChange={(e) => setKind(e.target.value)}
          className="rounded-lg border-border bg-surface text-sm text-content focus:border-accent focus:ring-accent">
          <option value="fact">fact</option>
          <option value="preference">preference</option>
          <option value="project">project</option>
        </select>
        <button onClick={add} className="rounded-lg bg-accent px-3 py-2 text-accent-fg hover:opacity-90">
          <Plus size={16} />
        </button>
      </div>

      <div className="space-y-2">
        {memories.length === 0 && <p className="text-sm text-muted">No memories yet.</p>}
        {memories.map((m) => (
          <div key={m.id} className="flex items-center gap-3 rounded-lg border border-border bg-surface px-3 py-2">
            <Brain size={16} className="shrink-0 text-accent" />
            <div className="min-w-0 flex-1">
              <div className="text-sm text-content">{m.content}</div>
              <span className={`mt-0.5 inline-block rounded px-1.5 py-0.5 text-[10px] ${KIND_COLORS[m.kind] || ''}`}>
                {m.kind}
              </span>
            </div>
            <button onClick={() => api.deleteMemory(m.id).then(load)}
              className="rounded p-1.5 text-muted hover:text-red-600" title="Forget">
              <Trash2 size={15} />
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}
