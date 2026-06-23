// Stream an SSE POST endpoint and dispatch events to a handler.
// onEvent receives the parsed event object ({type, ...}). Returns an abort fn.
import { authHeaders } from './token'

export function streamChat(payload, onEvent, onDone, onError, path = '/api/chat') {
  const controller = new AbortController()

  ;(async () => {
    try {
      const res = await fetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify(payload),
        signal: controller.signal,
      })
      if (!res.ok || !res.body) {
        // Surface the server's error detail (e.g. a 402 budget rejection) instead of a
        // bare status code, so the UI can show a meaningful message.
        let detail = ''
        try {
          const data = await res.clone().json()
          detail = data?.detail || data?.error?.message || ''
        } catch {
          /* non-JSON body */
        }
        const err = new Error(detail || `Chat failed: ${res.status}`)
        err.status = res.status
        throw err
      }
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const frames = buffer.split('\n\n')
        buffer = frames.pop() || ''
        for (const frame of frames) {
          const line = frame.split('\n').find((l) => l.startsWith('data: '))
          if (!line) continue
          try {
            onEvent(JSON.parse(line.slice(6)))
          } catch {
            /* ignore malformed frame */
          }
        }
      }
      onDone && onDone()
    } catch (err) {
      if (err.name !== 'AbortError') onError && onError(err)
      else onDone && onDone()
    }
  })()

  return () => controller.abort()
}
