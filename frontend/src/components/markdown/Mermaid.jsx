import { useEffect, useRef, useState } from 'react'

let _mermaid = null
let _id = 0

// Lazy-load mermaid (it's heavy) and render the diagram into an inline SVG.
export default function Mermaid({ code }) {
  const ref = useRef(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        if (!_mermaid) {
          const mod = await import('mermaid')
          _mermaid = mod.default
          _mermaid.initialize({ startOnLoad: false, theme: 'default', securityLevel: 'strict' })
        }
        const { svg } = await _mermaid.render(`mmd-${_id++}`, code)
        if (!cancelled && ref.current) ref.current.innerHTML = svg
      } catch (e) {
        if (!cancelled) setError(String(e.message || e))
      }
    })()
    return () => { cancelled = true }
  }, [code])

  if (error) {
    return (
      <pre className="my-3 overflow-x-auto rounded-lg border border-red-300 bg-red-50 p-3 text-xs text-red-700">
        Mermaid error: {error}
        {'\n\n'}{code}
      </pre>
    )
  }
  return <div ref={ref} className="my-3 flex justify-center overflow-x-auto rounded-lg border border-border bg-white p-3" />
}
