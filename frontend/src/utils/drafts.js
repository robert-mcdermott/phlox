import { useState } from 'react'

const prefix = 'phlox-draft:'
// Session storage survives refresh in this tab; never send unsent text to the server.
function read(key) {
  try { return sessionStorage.getItem(key) || '' } catch { return '' }
}
export function clearDrafts() {
  try {
    Object.keys(sessionStorage).filter(k => k.startsWith(prefix)).forEach(k => sessionStorage.removeItem(k))
  } catch { /* Storage may be disabled. */ }
}
export function useConversationDraft(owner, conversation) {
  const key = `${prefix}${owner}:${conversation || 'new'}`
  const [current, setCurrent] = useState(() => ({ key, text: read(key) }))
  const [error, setError] = useState('')
  const text = current.key === key ? current.text : read(key)
  const setText = (value) => {
    const next = typeof value === 'function' ? value(text) : value
    setCurrent({ key, text: next })
    try {
      if (next) sessionStorage.setItem(key, next)
      else sessionStorage.removeItem(key)
      setError('')
    } catch { setError('Draft recovery is unavailable in this browser. Keep this tab open until you send.') }
  }
  return [text, setText, error]
}
