// Thin REST client over the FastAPI backend.
import { authHeaders, setToken } from './token'

async function req(method, path, body) {
  const opts = { method, headers: { ...authHeaders() } }
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json'
    opts.body = JSON.stringify(body)
  }
  const res = await fetch(path, opts)
  if (res.status === 401) {
    setToken(null)
    window.dispatchEvent(new Event('phlox-unauthorized'))
  }
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`${res.status}: ${text}`)
  }
  if (res.status === 204) return null
  return res.json()
}

export const api = {
  // auth
  authConfig: () => req('GET', '/api/auth/config'),
  login: (username, password) => req('POST', '/api/auth/login', { username, password }),
  register: (body) => req('POST', '/api/auth/register', body),
  me: () => req('GET', '/api/auth/me'),
  listUsers: () => req('GET', '/api/auth/users'),
  createUser: (body) => req('POST', '/api/auth/users', body),
  updateUser: (id, body) => req('PATCH', `/api/auth/users/${id}`, body),
  deleteUser: (id) => req('DELETE', `/api/auth/users/${id}`),
  entraLoginUrl: () => req('GET', '/api/auth/entra/login'),

  // conversations
  listConversations: () => req('GET', '/api/conversations'),
  getConversation: (id) => req('GET', `/api/conversations/${id}`),
  createConversation: (body) => req('POST', '/api/conversations', body || {}),
  updateConversation: (id, body) => req('PATCH', `/api/conversations/${id}`, body),
  deleteConversation: (id) => req('DELETE', `/api/conversations/${id}`),
  // Delete a message and everything after it (edit / regenerate).
  truncateFrom: (conversationId, messageId) =>
    req('DELETE', `/api/conversations/${conversationId}/messages/${messageId}`),

  // providers + settings
  getProviders: () => req('GET', '/api/providers'),
  getModels: (profile) => req('GET', `/api/providers/${profile}/models`),
  testProfile: (profile) => req('POST', `/api/providers/${profile}/test`),
  getSettings: () => req('GET', '/api/settings'),
  updateSettings: (body) => req('PATCH', '/api/settings', body),
  getSuggestions: () => req('GET', '/api/settings/suggestions'),

  // documents
  listDocuments: (conversationId) =>
    req('GET', conversationId ? `/api/documents?conversation_id=${conversationId}` : '/api/documents'),
  deleteDocument: (id) => req('DELETE', `/api/documents/${id}`),
  uploadDocument: async (file, conversationId) => {
    const fd = new FormData()
    fd.append('file', file)
    if (conversationId) fd.append('conversation_id', conversationId)
    const res = await fetch('/api/documents', { method: 'POST', body: fd, headers: { ...authHeaders() } })
    if (!res.ok) throw new Error(await res.text())
    return res.json()
  },

  // assistants (reads for everyone; writes are admin-only server-side)
  listAssistants: () => req('GET', '/api/assistants'),
  createAssistant: (body) => req('POST', '/api/assistants', body),
  updateAssistant: (id, body) => req('PATCH', `/api/assistants/${id}`, body),
  deleteAssistant: (id) => req('DELETE', `/api/assistants/${id}`),
  listAssistantDocuments: (id) => req('GET', `/api/assistants/${id}/documents`),
  deleteAssistantDocument: (id, docId) => req('DELETE', `/api/assistants/${id}/documents/${docId}`),
  uploadAssistantDocument: async (id, file) => {
    const fd = new FormData()
    fd.append('file', file)
    const res = await fetch(`/api/assistants/${id}/documents`, {
      method: 'POST',
      body: fd,
      headers: { ...authHeaders() },
    })
    if (!res.ok) throw new Error(await res.text())
    return res.json()
  },

  // skills (users manage their own; admins can publish public ones)
  listSkills: () => req('GET', '/api/skills'),
  createSkill: (body) => req('POST', '/api/skills', body),
  updateSkill: (id, body) => req('PATCH', `/api/skills/${id}`, body),
  deleteSkill: (id) => req('DELETE', `/api/skills/${id}`),
  importSkill: async (file) => {
    const fd = new FormData()
    fd.append('file', file)
    const res = await fetch('/api/skills/import', { method: 'POST', body: fd, headers: { ...authHeaders() } })
    if (!res.ok) throw new Error(await res.text())
    return res.json()
  },
  exportSkillUrl: (id) => `/api/skills/${id}/export`,

  // mcp
  listMcp: () => req('GET', '/api/mcp'),
  addMcp: (body) => req('POST', '/api/mcp', body),
  connectMcp: (id) => req('POST', `/api/mcp/${id}/connect`),
  disconnectMcp: (id) => req('POST', `/api/mcp/${id}/disconnect`),
  deleteMcp: (id) => req('DELETE', `/api/mcp/${id}`),

  // tools
  listTools: () => req('GET', '/api/tools'),
  updateTool: (name, body) => req('PATCH', `/api/tools/${name}`, body),

  // admin deployment config (config.yml overlay; section = profiles|pricing|resilience|generation|sandbox|guardrails)
  getAdminConfig: () => req('GET', '/api/admin/config'),
  updateAdminConfig: (section, body) => req('PUT', `/api/admin/config/${section}`, body),
  previewGuardrails: (body) => req('POST', '/api/admin/config/guardrails/preview', body),

  // usage (self) + admin accounting (per user × model × month)
  usage: () => req('GET', '/api/usage'),
  usageByUser: ({ start, end } = {}) => {
    const qs = new URLSearchParams()
    if (start) qs.set('start', start)
    if (end) qs.set('end', end)
    const q = qs.toString()
    return req('GET', `/api/usage/by-user${q ? `?${q}` : ''}`)
  },

  // budgets (monthly spend caps): self status + admin CRUD
  budgetStatus: () => req('GET', '/api/usage/budget'),
  listBudgets: () => req('GET', '/api/admin/budgets'),
  createBudget: (body) => req('POST', '/api/admin/budgets', body),
  updateBudget: (id, body) => req('PATCH', `/api/admin/budgets/${id}`, body),
  deleteBudget: (id) => req('DELETE', `/api/admin/budgets/${id}`),

  // api keys (gateway access)
  listApiKeys: () => req('GET', '/api/api-keys'),
  createApiKey: (body) => req('POST', '/api/api-keys', body || {}),
  revokeApiKey: (id) => req('DELETE', `/api/api-keys/${id}`),

  // memories
  listMemories: () => req('GET', '/api/memories'),
  addMemory: (body) => req('POST', '/api/memories', body),
  deleteMemory: (id) => req('DELETE', `/api/memories/${id}`),

  // checkpoints (per conversation)
  listCheckpoints: (conversationId) => req('GET', `/api/checkpoints/${conversationId}`),
  restoreCheckpoint: (conversationId, sha) =>
    req('POST', `/api/checkpoints/${conversationId}/restore`, { sha }),

  // files
  fileUrl: (conversationId, path) =>
    `/api/files/${conversationId}?path=${encodeURIComponent(path)}`,
  listWorkspaceFiles: (conversationId) => req('GET', `/api/files/${conversationId}/list`),
  // Raw text content of a workspace file, for the artifact canvas preview.
  getFileText: async (conversationId, path) => {
    const res = await fetch(api.fileUrl(conversationId, path), { headers: { ...authHeaders() } })
    if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`)
    return res.text()
  },
}
