import { create } from 'zustand'
import { api } from '../api/client'
import { streamChat } from '../api/sse'
import { setToken } from '../api/token'
import { applyTheme, initialTheme } from '../theme/presets'
import { canvasKind } from '../utils/canvas'

// Shape of the in-progress assistant turn assembled from SSE events.
function emptyLive() {
  return { content: '', thinking: '', toolCalls: [], artifacts: [], status: '', pendingApproval: null }
}

export const useStore = create((set, get) => ({
  conversations: [],
  activeId: null,
  messages: [],
  settings: null,
  providers: [],
  assistants: [],
  // Registered agent skills (public + own), for the "/" composer picker.
  skills: [],
  // Welcome-screen starter prompts (deployment-wide, admin-editable). Null until loaded so
  // the Welcome grid can tell "not loaded yet" from "admin cleared the list".
  suggestions: null,
  // Assistant for the active conversation (pinned server-side) or the pending new chat.
  activeAssistantId: null,
  theme: initialTheme(),

  streaming: false,
  live: null, // emptyLive() while streaming
  abortFn: null,
  error: null,
  queued: null, // a follow-up queued while streaming {text, images, documentRefs, ...}
  lastUsage: null, // {input, output, total} token usage from the last model response
  budget: null, // monthly budget status for the signed-in user (or null when none applies)

  // -- artifact canvas -------------------------------------------------------
  // Side-panel preview of a workspace artifact (html/markdown/text), or null when closed.
  // { conversationId, path, name, ext, kind, nonce } — `nonce` bumps to force a re-fetch
  // when the same file is rewritten later in the same turn.
  canvas: null,

  // -- auth ----------------------------------------------------------------
  authConfig: null, // {enabled, allow_registration, entra_enabled}
  user: null, // the signed-in user (or a synthetic admin when auth disabled)
  authReady: false, // true once we've determined auth state

  async init() {
    applyTheme(get().theme)
    // Determine auth state first; only load app data once authenticated.
    let cfg = { enabled: false }
    try {
      cfg = await api.authConfig()
    } catch {
      /* backend may be starting */
    }
    set({ authConfig: cfg })

    if (!cfg.enabled) {
      set({ user: { username: 'local', role: 'admin' }, authReady: true })
      await get().loadApp()
      return
    }
    // Auth enabled: try to restore session.
    try {
      const user = await api.me()
      set({ user, authReady: true })
      await get().loadApp()
    } catch {
      setToken(null)
      set({ user: null, authReady: true })
    }
  },

  async loadApp() {
    await Promise.all([
      get().loadConversations(), get().loadSettings(), get().loadProviders(), get().loadBudget(),
      get().loadAssistants(), get().loadSkills(), get().loadSuggestions(),
    ])
  },

  async loadSuggestions() {
    try {
      const { suggestions } = await api.getSuggestions()
      set({ suggestions })
    } catch {
      set({ suggestions: [] })
    }
  },

  async loadSkills() {
    try {
      const skills = await api.listSkills()
      set({ skills })
    } catch {
      set({ skills: [] })
    }
  },

  async loadAssistants() {
    try {
      const assistants = await api.listAssistants()
      set({ assistants })
    } catch {
      set({ assistants: [] })
    }
  },

  // The active assistant object, or null (also null when it no longer resolves — deleted).
  activeAssistant() {
    const { assistants, activeAssistantId } = get()
    return assistants.find((a) => a.id === activeAssistantId) || null
  },

  // Pick an assistant for the next new chat (only meaningful on the Welcome screen).
  selectAssistant(id) {
    set({ activeAssistantId: id || null })
  },

  // Monthly budget status (drives the chat warning/block banner). Refreshed after each
  // turn since spend changes. Null'd out if the request fails or no budget applies.
  async loadBudget() {
    try {
      const b = await api.budgetStatus()
      set({ budget: b && b.budgets && b.budgets.length ? b : null })
    } catch {
      set({ budget: null })
    }
  },

  async login(username, password) {
    const { token, user } = await api.login(username, password)
    setToken(token)
    set({ user })
    await get().loadApp()
  },

  async registerAccount(body) {
    const { token, user } = await api.register(body)
    setToken(token)
    set({ user })
    await get().loadApp()
  },

  logout() {
    setToken(null)
    set({
      user: null, conversations: [], messages: [], activeId: null, live: null, canvas: null,
      assistants: [], activeAssistantId: null,
    })
  },

  async loadConversations() {
    const conversations = await api.listConversations()
    set({ conversations })
  },

  async loadSettings() {
    const settings = await api.getSettings()
    set({ settings })
    if (settings.theme && settings.theme !== get().theme) {
      set({ theme: settings.theme })
      applyTheme(settings.theme)
    }
  },

  async loadProviders() {
    try {
      const { profiles } = await api.getProviders()
      set({ providers: profiles })
    } catch {
      set({ providers: [] })
    }
  },

  // -- theme ---------------------------------------------------------------
  setTheme(theme) {
    set({ theme })
    applyTheme(theme)
    api.updateSettings({ theme }).catch(() => {})
  },

  async updateSettings(patch) {
    const settings = await api.updateSettings(patch)
    set({ settings })
    return settings
  },

  // -- conversation selection ---------------------------------------------
  async selectConversation(id) {
    if (get().streaming) get().stopStreaming()
    if (!id) {
      set({ activeId: null, messages: [], live: null, canvas: null, activeAssistantId: null })
      return
    }
    const conv = await api.getConversation(id)
    set({
      activeId: id,
      messages: conv.messages,
      live: null,
      canvas: null,
      activeAssistantId: conv.assistant_id || null,
    })
  },

  newConversation() {
    if (get().streaming) get().stopStreaming()
    set({ activeId: null, messages: [], live: null, error: null, canvas: null, activeAssistantId: null })
  },

  async deleteConversation(id) {
    await api.deleteConversation(id)
    await get().loadConversations()
    if (get().activeId === id) get().newConversation()
  },

  async renameConversation(id, title) {
    await api.updateConversation(id, { title })
    await get().loadConversations()
  },

  // Export a conversation as a Markdown file (downloaded client-side).
  async exportConversation(id) {
    const conv = await api.getConversation(id)
    let md = `# ${conv.title}\n\n_Exported ${new Date().toLocaleString()}_\n\n`
    for (const m of conv.messages) {
      if (m.role === 'user') {
        md += `### You\n\n${m.content}\n\n`
      } else if (m.role === 'assistant') {
        md += `### Assistant${m.model ? ` · ${m.model}` : ''}\n\n`
        for (const tc of m.tool_calls || []) {
          md += `> 🔧 **${tc.name}**(${JSON.stringify(tc.arguments)})\n>\n`
        }
        md += `${m.content || ''}\n\n`
      }
    }
    const blob = new Blob([md], { type: 'text/markdown' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${(conv.title || 'conversation').replace(/[^a-z0-9-_ ]/gi, '').slice(0, 50)}.md`
    a.click()
    URL.revokeObjectURL(url)
  },

  // -- sending a message ---------------------------------------------------
  async sendMessage(
    text,
    {
      autoApprove = true,
      webSearch = false,
      documentSearch = false,
      images = [],
      documentIds = [],
      documentRefs = [],
      skills = [],
      skillRefs = [],
      skillsEnabled = true,
    } = {},
  ) {
    if (!text.trim() && images.length === 0 && documentIds.length === 0 && skills.length === 0) return
    // Steering: if a turn is in flight, queue this as a follow-up to send when it finishes.
    if (get().streaming) {
      set({
        queued: {
          text, images, documentIds, documentRefs, autoApprove, webSearch, documentSearch,
          skills, skillRefs, skillsEnabled,
        },
      })
      return
    }
    const attachments = [
      ...images.map((url, idx) => ({ type: 'image', idx, url })),
      ...documentRefs.map((doc) => ({
        type: 'document',
        document_id: doc.id || doc.document_id,
        filename: doc.filename,
        mime: doc.mime,
        size_bytes: doc.size_bytes,
        n_chunks: doc.n_chunks,
        status: doc.status,
      })),
      ...skillRefs.map((s) => ({ type: 'skill', skill_id: s.id, name: s.name })),
    ]
    const userMsg = {
      id: `tmp-${Date.now()}`,
      role: 'user',
      content: text,
      attachments,
    }
    set((s) => ({
      messages: [...s.messages, userMsg],
      streaming: true,
      live: emptyLive(),
      error: null,
    }))

    const payload = {
      conversation_id: get().activeId,
      message: text,
      auto_approve: autoApprove,
      web_search: webSearch,
      document_search: documentSearch,
      document_ids: documentIds,
      images,
      skills,
      skills_enabled: skillsEnabled,
      // Only meaningful for a new conversation; the server pins it there and ignores
      // it on existing ones.
      assistant_id: get().activeAssistantId,
    }

    const abortFn = streamChat(
      payload,
      (ev) => get()._onEvent(ev),
      () => get()._finalize(),
      (err) => {
        set({ error: String(err).replace(/^Error:\s*/, ''), streaming: false, live: null })
        // A 402 budget rejection means spend/limit state may have changed — refresh banner.
        if (err?.status === 402) get().loadBudget()
      },
    )
    set({ abortFn })
  },

  // Re-run the last assistant turn (delete it server-side, then regenerate).
  async regenerate() {
    if (get().streaming) return
    const msgs = get().messages
    let idx = -1
    for (let i = msgs.length - 1; i >= 0; i--) {
      if (msgs[i].role === 'assistant') { idx = i; break }
    }
    if (idx === -1 || String(msgs[idx].id).startsWith('tmp-')) return
    await api.truncateFrom(get().activeId, msgs[idx].id)
    set((s) => ({ messages: s.messages.slice(0, idx), streaming: true, live: emptyLive(), error: null }))
    const abortFn = streamChat(
      { conversation_id: get().activeId, regenerate: true, auto_approve: true },
      (ev) => get()._onEvent(ev),
      () => get()._finalize(),
      (err) => set({ error: String(err), streaming: false, live: null }),
    )
    set({ abortFn })
  },

  // Edit a prior user message: drop it + everything after, then re-send the new text.
  async editMessage(messageId, newText) {
    if (get().streaming || !newText.trim()) return
    const idx = get().messages.findIndex((m) => m.id === messageId)
    if (idx === -1 || String(messageId).startsWith('tmp-')) return
    await api.truncateFrom(get().activeId, messageId)
    set((s) => ({ messages: s.messages.slice(0, idx) }))
    await get().sendMessage(newText)
  },

  _onEvent(ev) {
    set((s) => {
      if (!s.live) return {}
      const live = { ...s.live }
      let canvas
      switch (ev.type) {
        case 'conversation':
          return { activeId: ev.id }
        case 'token':
          live.content += ev.content
          live.status = ''
          break
        case 'thinking':
          live.thinking += ev.content
          break
        case 'status':
          live.status = ev.content
          break
        case 'usage':
          return {
            live: { ...live, usage: { input: ev.input || 0, output: ev.output || 0, total: ev.total || 0 } },
            lastUsage: { input: ev.input || 0, output: ev.output || 0, total: ev.total || 0 },
          }
        case 'tool_call': {
          // Upsert by id (resume may re-emit a previously-pending call).
          const exists = live.toolCalls.some((tc) => tc.id === ev.id)
          live.toolCalls = exists
            ? live.toolCalls.map((tc) =>
                tc.id === ev.id ? { ...tc, name: ev.name, arguments: ev.arguments } : tc,
              )
            : [
                ...live.toolCalls,
                {
                  id: ev.id, name: ev.name, arguments: ev.arguments,
                  content: null, is_error: false, artifacts: [], running: true,
                },
              ]
          break
        }
        case 'tool_progress':
          // Live partial output from a still-running tool (e.g. run_shell) — appended so
          // the tool card shows progress instead of staying blank until it finishes.
          // `running` stays true here (only tool_result clears it), so the spinner keeps
          // showing while output streams in.
          live.toolCalls = live.toolCalls.map((tc) =>
            tc.id === ev.id ? { ...tc, content: (tc.content || '') + ev.content } : tc,
          )
          break
        case 'tool_result':
          live.toolCalls = live.toolCalls.map((tc) =>
            tc.id === ev.id
              ? { ...tc, content: ev.content, is_error: ev.is_error, artifacts: ev.artifacts || [], running: false }
              : tc,
          )
          live.status = ''
          break
        case 'artifact': {
          live.artifacts = [...live.artifacts, { name: ev.name, path: ev.path, ext: ev.ext, url: ev.url }]
          // Auto-open the canvas the first time a viewable (html/markdown/text) artifact
          // shows up; if it's already open on this same file, bump nonce to re-fetch the
          // latest content (e.g. the agent iterated on the same file).
          const kind = canvasKind(ev.ext)
          if (kind) {
            if (!s.canvas) {
              canvas = { conversationId: s.activeId, path: ev.path, name: ev.name, ext: ev.ext, kind, nonce: Date.now() }
            } else if (s.canvas.conversationId === s.activeId && s.canvas.path === ev.path) {
              canvas = { ...s.canvas, nonce: Date.now() }
            }
          }
          break
        }
        case 'approval_request':
          live.pendingApproval = { pendingId: ev.pending_id, calls: ev.calls }
          live.status = ''
          break
        case 'error':
          return { error: ev.content }
        default:
          break
      }
      return canvas !== undefined ? { live, canvas } : { live }
    })
  },

  // Open the canvas on a workspace artifact (e.g. from the "View" button on an artifact
  // chip, or a file in the workspace files modal). No-op for non-viewable extensions.
  openCanvasArtifact(art, conversationId) {
    const kind = canvasKind(art.ext)
    if (!kind) return
    set({ canvas: { conversationId, path: art.path, name: art.name, ext: art.ext, kind, nonce: Date.now() } })
  },

  closeCanvas() {
    set({ canvas: null })
  },

  async _finalize() {
    set({ streaming: false, abortFn: null })
    // If the turn paused for approval, keep the live message + approval prompt visible.
    if (get().live?.pendingApproval) return

    const id = get().activeId
    // Reconcile with the server's persisted state (canonical message + title).
    if (id) {
      try {
        const conv = await api.getConversation(id)
        set({ messages: conv.messages, live: null })
      } catch {
        set({ live: null })
      }
    } else {
      set({ live: null })
    }
    get().loadConversations()
    // Spend changed this turn — refresh the budget banner.
    get().loadBudget()

    // Send any follow-up the user queued while this turn was streaming.
    const q = get().queued
    if (q) {
      set({ queued: null })
      get().sendMessage(q.text, {
        autoApprove: q.autoApprove,
        webSearch: q.webSearch,
        documentSearch: q.documentSearch,
        images: q.images,
        documentIds: q.documentIds || [],
        documentRefs: q.documentRefs || [],
        skills: q.skills || [],
        skillRefs: q.skillRefs || [],
        skillsEnabled: q.skillsEnabled !== false,
      })
    }
  },

  clearQueued() {
    set({ queued: null })
  },

  // Approve/deny the tools in the current pending approval and resume the turn.
  async resolveApproval(decisions) {
    const live = get().live
    if (!live?.pendingApproval) return
    const pendingId = live.pendingApproval.pendingId
    // Clear the prompt and show running state again.
    set({ streaming: true, error: null, live: { ...live, pendingApproval: null, status: '' } })

    const abortFn = streamChat(
      { pending_id: pendingId, decisions },
      (ev) => get()._onEvent(ev),
      () => get()._finalize(),
      (err) => set({ error: String(err), streaming: false }),
      '/api/chat/approve',
    )
    set({ abortFn })
  },

  stopStreaming() {
    const { abortFn } = get()
    if (abortFn) abortFn()
    set({ streaming: false, abortFn: null, live: null })
  },
}))
