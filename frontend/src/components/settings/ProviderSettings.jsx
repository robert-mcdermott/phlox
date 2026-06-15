import { useEffect, useState } from 'react'
import { CheckCircle, XCircle, Loader2 } from 'lucide-react'
import { useStore } from '../../store/useStore'
import { api } from '../../api/client'

export default function ProviderSettings() {
  const providers = useStore((s) => s.providers)
  const settings = useStore((s) => s.settings)
  const updateSettings = useStore((s) => s.updateSettings)
  const loadSettings = useStore((s) => s.loadSettings)
  const isAdmin = useStore((s) => s.user?.role === 'admin')

  const [profile, setProfile] = useState(settings?.active_profile || '')
  const [model, setModel] = useState(settings?.model || '')
  const [models, setModels] = useState([])
  const [test, setTest] = useState(null)
  const [testing, setTesting] = useState(false)
  const [form, setForm] = useState({
    system_prompt: settings?.system_prompt || '',
    temperature: settings?.temperature ?? 0.3,
    max_tokens: settings?.max_tokens ?? 8192,
    max_context_tokens: settings?.max_context_tokens ?? 16000,
    max_tool_rounds: settings?.max_tool_rounds ?? 12,
  })

  useEffect(() => {
    if (!profile) return
    api.getModels(profile).then((r) => setModels(r.models || [])).catch(() => setModels([]))
  }, [profile])

  const saveProfile = async (p) => {
    setProfile(p)
    const prov = providers.find((x) => x.name === p)
    const m = prov?.model || ''
    setModel(m)
    await updateSettings({ active_profile: p, model: m })
  }

  const saveModel = async (m) => {
    setModel(m)
    await updateSettings({ model: m })
  }

  const saveForm = async (patch) => {
    const next = { ...form, ...patch }
    setForm(next)
    await updateSettings(patch)
  }

  const runTest = async () => {
    setTesting(true)
    setTest(null)
    try {
      setTest(await api.testProfile(profile))
    } catch (e) {
      setTest({ ok: false, error: String(e) })
    } finally {
      setTesting(false)
      loadSettings()
    }
  }

  if (!providers.length) {
    return (
      <div className="rounded-lg border border-border bg-surface p-4 text-sm text-muted">
        No provider profiles found.{' '}
        {isAdmin ? (
          <>Add one in <b>Admin → Configuration</b> (no restart needed), or seed{' '}
          <code className="rounded bg-surface-3 px-1">backend/config.yml</code> from{' '}
          <code className="rounded bg-surface-3 px-1">config.yml.example</code>.</>
        ) : (
          <>Ask an administrator to add a provider profile.</>
        )}
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <Section title="Provider profile" hint="Switch between Bedrock, OpenAI, or any local/compatible endpoint.">
        <select
          value={profile}
          onChange={(e) => saveProfile(e.target.value)}
          className="w-full rounded-lg border-border bg-surface text-content focus:border-accent focus:ring-accent"
        >
          {providers.map((p) => (
            <option key={p.name} value={p.name}>
              {p.label} ({p.type})
            </option>
          ))}
        </select>
        <div className="mt-2 flex items-center gap-2">
          <button
            onClick={runTest}
            disabled={testing}
            className="rounded-lg border border-border px-3 py-1.5 text-sm text-content hover:border-accent"
          >
            {testing ? <Loader2 size={14} className="animate-spin" /> : 'Test connection'}
          </button>
          {test && (
            <span className={`flex items-center gap-1 text-sm ${test.ok ? 'text-green-600' : 'text-red-600'}`}>
              {test.ok ? <CheckCircle size={15} /> : <XCircle size={15} />}
              {test.ok ? `OK${test.sample ? `: "${test.sample}"` : ''}` : test.error}
            </span>
          )}
        </div>
      </Section>

      <Section title="Model">
        <select
          value={model}
          onChange={(e) => saveModel(e.target.value)}
          className="w-full rounded-lg border-border bg-surface text-content focus:border-accent focus:ring-accent"
        >
          {(models.length ? models : [model].filter(Boolean)).map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
      </Section>

      <Section title="System prompt">
        <textarea
          value={form.system_prompt}
          onChange={(e) => setForm({ ...form, system_prompt: e.target.value })}
          onBlur={() => saveForm({ system_prompt: form.system_prompt })}
          rows={5}
          className="w-full rounded-lg border-border bg-surface text-sm text-content focus:border-accent focus:ring-accent"
        />
      </Section>

      <div className="grid grid-cols-2 gap-3">
        <NumberField label="Temperature" step="0.1" min="0" max="2" value={form.temperature}
          onChange={(v) => saveForm({ temperature: parseFloat(v) })} />
        <NumberField label="Max output tokens (per response)" step="256" min="256" value={form.max_tokens}
          onChange={(v) => saveForm({ max_tokens: parseInt(v, 10) })} />
        <NumberField label="Max context tokens (conversation)" step="1000" min="2000" value={form.max_context_tokens}
          onChange={(v) => saveForm({ max_context_tokens: parseInt(v, 10) })} />
        <NumberField label="Max tool rounds" step="1" min="1" value={form.max_tool_rounds}
          onChange={(v) => saveForm({ max_tool_rounds: parseInt(v, 10) })} />
      </div>
      <p className="mt-1 text-xs text-muted">
        <b>Max output tokens</b> caps a single response (raise it for large files or
        heavy-reasoning models). <b>Max context tokens</b> is the conversation size sent
        each turn — older turns are summarized beyond it.
      </p>
    </div>
  )
}

function Section({ title, hint, children }) {
  return (
    <div>
      <h3 className="mb-1 text-sm font-semibold text-content">{title}</h3>
      {hint && <p className="mb-2 text-xs text-muted">{hint}</p>}
      {children}
    </div>
  )
}

function NumberField({ label, value, onChange, ...rest }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-muted">{label}</span>
      <input
        type="number"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        {...rest}
        className="w-full rounded-lg border-border bg-surface text-sm text-content focus:border-accent focus:ring-accent"
      />
    </label>
  )
}
