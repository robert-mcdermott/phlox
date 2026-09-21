import { authHeaders, authFetch } from './token'

export async function runRequest(path, body, key) {
  const response = await authFetch(path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { ...authHeaders(), 'Content-Type': 'application/json', ...(key ? { 'Idempotency-Key': key } : {}) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  })
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    const error = new Error(data.detail || `Run request failed (${response.status})`)
    error.status = response.status
    throw error
  }
  return response.json()
}

// Reconnect with the last applied sequence. Fetch streaming supports bearer auth, unlike
// EventSource. Detaching cancels only this subscription; Stop is a separate POST action.
export function subscribeRun(id, onEvent, onDone, onError) {
  const controller = new AbortController()
  let cursor = 0
  ;(async () => {
    let delay = 500
    while (!controller.signal.aborted) {
      try {
        const response = await authFetch(`/api/runs/${id}/events?after=${cursor}`, {
          headers: authHeaders(), signal: controller.signal,
        })
        if (!response.ok) {
          const err = new Error(`Progress connection failed (${response.status})`)
          err.status = response.status
          throw err
        }
        const reader = response.body.getReader()
        const decoder = new TextDecoder()
        let buffer = '', finished = false
        try {
          while (!controller.signal.aborted) {
            const { done, value } = await reader.read()
            if (done) break
            buffer += decoder.decode(value, { stream: true })
            let end
            while ((end = buffer.indexOf('\n\n')) >= 0) {
              const frame = buffer.slice(0, end)
              buffer = buffer.slice(end + 2)
              const data = frame.split('\n').find((line) => line.startsWith('data: '))
              if (!data) continue
              const event = JSON.parse(data.slice(6))
              if (event.seq) {
                if (event.run_id !== id || event.seq <= cursor) continue
                onEvent(event)
                cursor = event.seq
              } else {
                onEvent(event)
              }
              if (event.type === 'run_state' && !['queued', 'running', 'cancel_requested'].includes(event.status)) finished = true
            }
          }
        } finally {
          await reader.cancel().catch(() => {})
        }
        if (finished) { if (!controller.signal.aborted) onDone(); return }
        throw new Error('Progress connection closed; reconnecting…')
      } catch (error) {
        if (controller.signal.aborted) return
        if ([400, 401, 403, 404, 410].includes(error.status)) { onError(error); return }
        onEvent({ type: 'connection', content: 'Connection lost. Reconnecting to saved progress…' })
        await new Promise((resolve) => {
          const finish = () => { clearTimeout(timer); controller.signal.removeEventListener('abort', finish); resolve() }
          const timer = setTimeout(finish, delay)
          controller.signal.addEventListener('abort', finish, { once: true })
        })
        delay = Math.min(delay * 2, 5000)
      }
    }
  })()
  return () => controller.abort()
}
