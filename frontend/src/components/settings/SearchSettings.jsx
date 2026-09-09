import { useEffect, useState } from 'react'
import { api } from '../../api/client'

const defaults = { engine: 'ddg', searxng_url: '', interval_seconds: 2 }
const input = 'mt-1 w-full rounded-lg border-border bg-surface-2 text-sm text-content'

export default function SearchSettings({ config, onSaved }) {
  const [form, setForm] = useState({ ...defaults, ...config, serper_api_key: '' })
  const [busy, setBusy] = useState(false)
  const [feedback, setFeedback] = useState('')
  useEffect(() => { setForm({ ...defaults, ...config, serper_api_key: '' }) }, [config])
  const change = (key, value) => { setForm(f => ({ ...f, [key]: value })); setFeedback('') }
  const action = async (test) => {
    setBusy(true); setFeedback('')
    try {
      if (test) {
        const result = await api.testWebSearch(form)
        setFeedback(result.ok
          ? `${result.fallback_reason ? `Fallback used: ${result.fallback_reason} ` : ''}${result.engine} returned ${result.result_count} results. Settings have not been saved.`
          : result.error)
      } else {
        onSaved(await api.updateAdminConfig('web-search', form))
        setFeedback('Saved. New searches use these settings immediately.')
      }
    } catch (e) { setFeedback(e.message) }
    finally { setBusy(false) }
  }
  return <section className="rounded-lg border border-border bg-surface p-4" aria-label="Web search configuration">
    <h4 className="text-sm font-semibold text-content">Web search</h4>
    <p className="mt-1 text-xs text-muted">DuckDuckGo works without a key and remains the fallback if the selected service fails. Search queries are sent to the selected service and, on failure, DuckDuckGo.</p>
    <div className="mt-3 grid gap-3 sm:grid-cols-2">
      <label className="text-xs text-content">Search engine
        <select aria-label="Search engine" className={input} value={form.engine} onChange={e => change('engine', e.target.value)}>
          <option value="ddg">DuckDuckGo (default)</option><option value="serper">Serper (Google results)</option><option value="searxng">SearXNG</option>
        </select>
      </label>
      <label className="text-xs text-content">Minimum seconds between searches
        <input className={input} type="number" min="1" max="30" step="0.5" value={form.interval_seconds} onChange={e => change('interval_seconds', Number(e.target.value))} />
      </label>
      {form.engine === 'serper' && <label className="text-xs text-content sm:col-span-2">Serper API key
        <input className={input} type="password" autoComplete="new-password" value={form.serper_api_key} placeholder={config?.serper_api_key_set ? 'Key stored — leave blank to keep it' : 'Enter your Serper API key'} onChange={e => change('serper_api_key', e.target.value)} />
        <span className="mt-1 block text-muted">The key is write-only in this console. Serper searches consume API credits.</span>
      </label>}
      {form.engine === 'searxng' && <label className="text-xs text-content sm:col-span-2">SearXNG instance URL
        <input className={input} type="url" placeholder="https://search.example.org" value={form.searxng_url} onChange={e => change('searxng_url', e.target.value)} />
        <span className="mt-1 block text-muted">Use a public instance from <a className="text-accent underline" href="https://searx.space/" target="_blank" rel="noreferrer">searx.space</a> that permits JSON searches. Hosting your own instance is optional. Public instances may block automated searches; use Test before saving.</span>
      </label>}
      {config?.serper_api_key_set && <label className="flex items-center gap-2 text-xs text-content sm:col-span-2"><input type="checkbox" checked={!!form.clear_serper_api_key} onChange={e => change('clear_serper_api_key', e.target.checked)} /> Remove stored Serper key on save (select another engine first)</label>}
    </div>
    <div className="mt-3 flex gap-2">
      <button disabled={busy} onClick={() => action(false)} className="rounded-lg bg-accent px-3 py-1.5 text-sm text-accent-fg disabled:opacity-50">Save search settings</button>
      <button disabled={busy} onClick={() => action(true)} className="rounded-lg border border-border px-3 py-1.5 text-sm text-content disabled:opacity-50">{busy ? 'Working…' : 'Test search'}</button>
    </div>
    <p className="mt-2 text-xs text-muted">Test sends a fixed public query using these form values, may consume one Serper credit, and reports any fallback. It does not save settings.</p>
    {feedback && <p role="status" className="mt-2 text-sm text-content">{feedback}</p>}
  </section>
}
