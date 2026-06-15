// Central auth-token store. Persisted in localStorage; injected into all API calls.
const KEY = 'phlox-token'
let token = null
try {
  token = localStorage.getItem(KEY)
} catch {
  /* ignore */
}

export function getToken() {
  return token
}

export function setToken(t) {
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
