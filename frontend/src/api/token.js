// Central auth-token store. Persisted in localStorage; injected into all API calls.
const KEY = 'phlox-token'
let token = null
let version = 0
try {
  token = localStorage.getItem(KEY)
} catch {
  /* ignore */
}

export function getToken() {
  return token
}

export function setToken(t) {
  version += 1
  token = t || null
  try {
    if (t) localStorage.setItem(KEY, t)
    else localStorage.removeItem(KEY)
  } catch {
    /* ignore */
  }
}

export function authHeaders() {
  return token ? { Authorization: `Bearer ${token}` } : {}
}

export function authVersion() {
  return version
}

// Only the session that sent a request can be invalidated by its response. Several
// requests may fail together, or a slow old response may arrive after a fresh login.
export async function authFetch(path, options = {}) {
  const sentVersion = version
  const headers = new Headers(options.headers)
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const response = await fetch(path, { ...options, headers })
  if (response.status === 401 && sentVersion === version && token) {
    setToken(null)
    window.dispatchEvent(new Event('phlox-unauthorized'))
  }
  return response
}
