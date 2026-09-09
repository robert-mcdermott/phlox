import { create } from 'zustand'
import { clearDrafts } from '../utils/drafts'
import { api } from '../api/client'
import { streamChat } from '../api/sse'
import { runRequest, subscribeRun } from '../api/runs'
import { setToken } from '../api/token'
import { applyTheme, initialTheme } from '../theme/presets'
import { canvasKind } from '../utils/canvas'

// Shape of the in-progress assistant turn assembled from SSE events.
function emptyLive() {
  return { research: null, sources: [], content: '', thinking: '', toolCalls: [], artifacts: [], status: '', pendingApproval: null }
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

  runCreation: null,
  run: null,
  connection: null,
  streamVersion: 0,
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
  user: null, // signed-in user; must_change_password gates the app until setup is complete
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
      if (!user.must_change_password) await get().loadApp()
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
    const owner = get().user
    try {
      const skills = await api.listSkills()
      if (get().user !== owner) return
      set({ skills })
    } catch {
      if (get().user !== owner) return
      set({ skills: [] })
    }
  },

  async loadAssistants() {
    const owner = get().user
    try {
      const assistants = await api.listAssistants()
      if (get().user !== owner) return
      set({ assistants })
    } catch {
      if (get().user !== owner) return
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
    const owner = get().user
    try {
      const b = await api.budgetStatus()
      if (get().user !== owner) return
      set({ budget: b && b.budgets && b.budgets.length ? b : null })
    } catch {
      if (get().user !== owner) return
      set({ budget: null })
    }
  },

  async login(username, password) {
    const { token, user } = await api.login(username, password)
    setToken(token)
    set({ user })
    if (!user.must_change_password) await get().loadApp()
  },

  async registerAccount(body) {
    const { token, user } = await api.register(body)
    setToken(token)
    set({ user })
    await get().loadApp()
  },

  async completeEntraLogin(handoff) {
    const { token, user } = await api.entraComplete(handoff)
    setToken(token)
    set({ user })
    await get().loadApp()
  },

  async changePassword(currentPassword, newPassword) {
    const user = await api.changePassword(currentPassword, newPassword)
    set({ user })
    await get().loadApp()
  },

  logout() {
    clearDrafts()
    get().detachStream()
    setToken(null)
    set({
      streamVersion: get().streamVersion + 1,
      user: null, conversations: [], messages: [], activeId: null, live: null, canvas: null,
      assistants: [], activeAssistantId: null, skills: [], providers: [], settings: null,
      budget: null, lastUsage: null, error: null,
    })
  },

  async loadConversations() {
    const user = get().user
    const conversations = await api.listConversations()
    if (get().user === user) set({ conversations })
  },

  async loadSettings() {
    const owner = get().user
    const settings = await api.getSettings()
    if (get().user !== owner) return
    set({ settings })
    if (settings.theme && settings.theme !== get().theme) {
      set({ theme: settings.theme })
      applyTheme(settings.theme)
    }
  },

  async loadProviders() {
    const owner = get().user
    try {
      const { profiles } = await api.getProviders()
      if (get().user !== owner) return
      set({ providers: profiles })
    } catch {
      if (get().user !== owner) return
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
    get().detachStream()
    const version = get().streamVersion + 1
    set({ streamVersion: version, activeId: id || null, messages: [], live: null, canvas: null, error: null, activeAssistantId: null })
    if (!id) return
    try {
      const conv = await api.getConversation(id)
      if (get().streamVersion !== version || get().activeId !== id) return
      set({ messages: conv.messages, activeAssistantId: conv.assistant_id || null })
      if (!await get().recoverRun(id, version)) await get().recoverApproval(id, version)
    } catch (err) {
      if (get().streamVersion === version) set({ error: String(err) })
    }
  },

  newConversation() {
    get().detachStream()
    set((s) => ({ streamVersion: s.streamVersion + 1, activeId: null, messages: [], live: null, error: null, canvas: null, activeAssistantId: null }))
  },

  async recoverApproval(id = get().activeId, version = get().streamVersion) {
    if (!id) return
    try {
      const approvals = await api.listApprovals(id)
      if (get().activeId !== id || get().streamVersion !== version || get().streaming) return
      const p = approvals[0]
      set({ live: p ? {
        ...emptyLive(), research: p.research || null, content: p.content || '', toolCalls: p.tool_steps || [],
        artifacts: p.artifacts || [], usage: p.usage, sources: p.sources || [],
        pendingApproval: { pendingId: p.pending_id, calls: p.calls, status: p.status, expiresAt: p.expires_at },
      } : null })
    } catch (err) {
      if (get().activeId === id && get().streamVersion === version) set({ error: String(err) })
    }
  },

  async refreshApproval() {
    if (get().streaming) return
    set({ error: null })
    await get()._finalize()
  },

  async dismissApproval() {
    const p = get().live?.pendingApproval
    if (!p || get().streaming) return
    const version = get().streamVersion
    try {
      await api.dismissApproval(p.pendingId)
      if (get().streamVersion !== version) return
      set({ live: null, error: null })
      await get()._finalize(version)
    } catch (err) {
      if (get().streamVersion === version) set({ error: String(err) })
    }
  },

  async deleteConversation(id) {
    try {
      await api.deleteConversation(id)
      await get().loadConversations()
      if (get().activeId === id) get().newConversation()
    } catch (error) { set({ error: error.message }) }
  },

  async renameConversation(id, title) {
    await api.updateConversation(id, { title })
    await get().loadConversations()
  },

  // Export a conversation as a Markdown file (downloaded client-side).
  async exportConversation(id) {
    const conv = await api.getConversation(id)
    const { markdown: md } = await api.exportConversation(id)
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
      autoApprove = false,
      webSearch = false,
      documentSearch = false,
      images = [],
      documentIds = [],
      documentRefs = [],
      skills = [],
      skillRefs = [],
      skillsEnabled = true,
      research = null,
    } = {},
  ) {
    if (!text.trim() && images.length === 0 && documentIds.length === 0 && skills.length === 0) return
    // Steering: if a turn is in flight, queue this as a follow-up to send when it finishes.
    if (get().streaming) {
      set({
        queued: {
          text, images, documentIds, documentRefs, autoApprove, webSearch, documentSearch,
          skills, skillRefs, skillsEnabled, research,
        },
      })
      return
    }
    if (get().run?.needs_acknowledgement) {
      set({ error: 'Review and acknowledge the interrupted run before continuing.' })
      return
    }
    if (get().live?.pendingApproval) {
      set({ error: 'Resolve or dismiss the pending approval before sending another message.' })
      return
    }
    const attachments = [
      ...(research ? [{ type: 'research', ...research }] : []),
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
      research,
      // Only meaningful for a new conversation; the server pins it there and ignores
      // it on existing ones.
      assistant_id: get().activeAssistantId,
    }

    get()._startStream(payload)
  },

  // Re-run the last assistant turn (delete it server-side, then regenerate).
  async regenerate() {
    if (get().streaming || get().run?.needs_acknowledgement || get().live?.pendingApproval) return
    const msgs = get().messages
    let idx = -1
    for (let i = msgs.length - 1; i >= 0; i--) {
      if (msgs[i].role === 'assistant') { idx = i; break }
    }
    if (idx === -1 || String(msgs[idx].id).startsWith('tmp-')) return
    const version = get().streamVersion
    await api.truncateFrom(get().activeId, msgs[idx].id)
    if (get().streamVersion !== version) return
    set((s) => ({ messages: s.messages.slice(0, idx), streaming: true, live: emptyLive(), error: null }))
    get()._startStream({ conversation_id: get().activeId, regenerate: true, auto_approve: false })
  },

  // Edit a prior user message: drop it + everything after, then re-send the new text.
  async editMessage(messageId, newText) {
    if (get().streaming || get().run?.needs_acknowledgement || get().live?.pendingApproval || !newText.trim()) return
    const idx = get().messages.findIndex((m) => m.id === messageId)
    if (idx === -1 || String(messageId).startsWith('tmp-')) return
    const version = get().streamVersion
    const previousAttachments = get().messages[idx].attachments || []
    const research = previousAttachments.find(a => a.type === 'research')
    const documents = previousAttachments.filter(a => a.type === 'document')
    await api.truncateFrom(get().activeId, messageId)
    if (get().streamVersion !== version) return
    set((s) => ({ messages: s.messages.slice(0, idx) }))
    await get().sendMessage(newText, research ? {
      research: { scope: research.scope, depth: research.depth, domains: research.domains || [] },
      documentIds: documents.map(d => d.document_id), documentRefs: documents,
    } : {})
  },

  _onEvent(ev) {
    set((s) => {
      if (!s.live) return {}
      const live = { ...s.live }
      let canvas
      switch (ev.type) {
        case 'connection':
          return { connection: ev.content }
        case 'run_state':
          return { run: ev, connection: null }

        case 'conversation':
          return { activeId: ev.id }
        case 'sources':
          live.sources = ev.sources || []
          break
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
        case 'research':
          live.research = ev
          break
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

  // Every callback belongs to one stream generation. Old network events cannot change
  // the selected conversation after Stop, navigation, or a newer stream starts.
  _startStream(payload, path = '/api/chat') {
    if (get().authConfig?.runs_enabled || (path.endsWith('/approve') && get().run)) {
      get()._startRun(payload, path)
      return
    }
    const version = get().streamVersion + 1
    set({ streamVersion: version })
    const abortFn = streamChat(
      payload,
      (ev) => { if (get().streamVersion === version) get()._onEvent(ev) },
      () => get()._finalize(version),
      async (err) => {
        if (get().streamVersion !== version) return
        set({ error: String(err).replace(/^Error:\s*/, ''), streaming: false, abortFn: null })
        if (err?.status === 402) get().loadBudget()
        await get()._finalize(version)
      },
      path,
    )
    set({ abortFn })
  },

  async _finalize(version = get().streamVersion) {
    if (get().streamVersion !== version) return
    set({ streaming: false, abortFn: null })
    const id = get().activeId
    if (id) {
      try {
        const conv = await api.getConversation(id)
        if (get().streamVersion !== version || get().activeId !== id) return
        set({ messages: conv.messages })
        if (get().authConfig?.run_history_available || get().authConfig?.runs_enabled) {
          const run = await runRequest(`/api/runs?conversation_id=${encodeURIComponent(id)}`)
          if (get().streamVersion !== version) return
          set({ run })
          if (run && ['queued', 'running', 'cancel_requested'].includes(run.status)) {
            set({ streaming: true, live: emptyLive() })
            get()._subscribeRun(run, version)
            return
          }
        }
        await get().recoverApproval(id, version)
      } catch (err) {
        if (get().streamVersion === version) set({ error: String(err) })
      }
    } else {
      set({ live: null })
    }
    if (get().streamVersion !== version) return
    get().loadConversations()
    get().loadBudget()
    if (get().live?.pendingApproval || get().error || (get().run && get().run.status !== 'completed')) return
    const q = get().queued
    if (q) {
      set({ queued: null })
      get().sendMessage(q.text, q)
    }
  },

  clearQueued() {
    set({ queued: null })
  },

  // Approve/deny the tools in the current pending approval and resume the turn.
  async resolveApproval(decisions) {
    const live = get().live
    if (get().streaming || !live?.pendingApproval || (live.pendingApproval.status || 'pending') !== 'pending') return
    const pendingId = live.pendingApproval.pendingId
    set({ streaming: true, error: null, live: { ...live, pendingApproval: null, status: '' } })
    get()._startStream({ pending_id: pendingId, decisions }, '/api/chat/approve')
  },

  async recoverRun(id = get().activeId, version = get().streamVersion) {
    if (!id || !(get().authConfig?.run_history_available || get().authConfig?.runs_enabled)) return false
    const run = await runRequest(`/api/runs?conversation_id=${encodeURIComponent(id)}`)
    if (get().streamVersion !== version || get().activeId !== id) return true
    set({ run })
    if (!run) return false
    if (['queued', 'running', 'cancel_requested'].includes(run.status)) {
      set({ streaming: true, live: emptyLive() })
      if (run.queued_message) set((s) => ({ messages: [...s.messages, { id: 'queued-run', role: 'user', content: run.queued_message }] }))
      get()._subscribeRun(run, version)
      return true
    }
    if (run.status === 'interrupted' && !run.events_expired) {
      set({ live: emptyLive() })
      get()._subscribeRun(run, version)
      return true
    }
    const conv = await api.getConversation(id)
    if (get().streamVersion === version && get().activeId === id) set({ messages: conv.messages })
    return false
  },

  async _startRun(payload, path) {
    const version = get().streamVersion + 1
    const approvalRun = path.endsWith('/approve') ? get().run : null
    const creation = { stop: false }
    set({ streamVersion: version, streaming: true, connection: null, runCreation: creation })
    const key = crypto.randomUUID()
    try {
      const url = approvalRun ? `/api/runs/${approvalRun.id}/approve` : '/api/runs'
      // A lost acceptance response can be retried safely with exactly the same key/body.
      let run
      try { run = await runRequest(url, payload, key) }
      catch (error) {
        if (error.status) throw error
        if (get().streamVersion !== version) return
        run = await runRequest(url, payload, key)
      }
      if (creation.stop) run = await runRequest(`/api/runs/${run.id}/cancel`, {})
      if (get().streamVersion !== version) return
      set({ run, runCreation: null, activeId: run.conversation_id, live: emptyLive() })
      get().loadConversations()
      get()._subscribeRun(run, version)
    } catch (error) {
      if (get().streamVersion !== version) return
      if (error.status === 401) { get().logout(); return }
      set({ error: String(error.message), streaming: false, runCreation: null })
      await get()._finalize(version)
    }
  },

  _subscribeRun(run, version) {
    const abortFn = subscribeRun(run.id,
      (event) => { if (get().streamVersion === version) get()._onEvent(event) },
      async () => {
        if (get().streamVersion !== version) return
        const current = get().run
        set({ streaming: false, abortFn: null })
        if (current?.status === 'interrupted') {
          const conv = await api.getConversation(current.conversation_id).catch(() => null)
          if (conv && get().streamVersion === version) set({ messages: conv.messages })
          return // Keep saved partial progress visible until explicitly acknowledged.
        }
        await get()._finalize(version)
      },
      async (error) => {
        if (get().streamVersion !== version) return
        if (error.status === 401) { get().logout(); return }
        set({ streaming: false, abortFn: null, error: error.message })
        await get()._finalize(version)
      },
    )
    set({ abortFn })
  },

  async acknowledgeRun() {
    const run = get().run, version = get().streamVersion
    if (!run?.needs_acknowledgement) return
    try {
      const updated = await runRequest(`/api/runs/${run.id}/acknowledge`, {})
      if (get().streamVersion !== version) return
      set({ run: updated, live: null, error: null })
      await get()._finalize(version)
    } catch (error) {
      if (get().streamVersion === version) set({ error: error.message })
    }
  },

  detachStream() {
    const { abortFn, streamVersion } = get()
    set({ streamVersion: streamVersion + 1, streaming: false, abortFn: null, live: null, queued: null, run: null, runCreation: null, connection: null })
    abortFn?.()
  },

  async stopStreaming() {
    if (get().runCreation) {
      get().runCreation.stop = true
      set({ queued: null, connection: 'Stop requested; waiting for the server to accept the request…' })
      return
    }
    const run = get().run, version = get().streamVersion
    if (run && ['queued', 'running', 'cancel_requested'].includes(run.status)) {
      set({ queued: null })
      try {
        const updated = await runRequest(`/api/runs/${run.id}/cancel`, {})
        if (get().streamVersion === version) set({ run: updated })
      } catch (error) {
        if (get().streamVersion === version) set({ error: error.message })
      }
      return
    }
    const { abortFn, streamVersion } = get()
    set({ streamVersion: streamVersion + 1, streaming: false, abortFn: null, live: null, queued: null })
    if (abortFn) {
      abortFn()
      get()._finalize(streamVersion + 1)
    }
  },
}))
