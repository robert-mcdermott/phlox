import { useEffect, useState } from 'react'
import {
  SlidersHorizontal, Loader2, Plus, Trash2, Save, Check, X, FlaskConical,
  Cpu, DollarSign, ShieldAlert, Box,
} from 'lucide-react'
import { api } from '../../api/client'

// Admin-only deployment configuration: a live overlay on backend/config.yml. Each section
// (providers, pricing, runtime, sandbox) saves independently and applies without a restart.
export default function ConfigPanel() {
  const [cfg, setCfg] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const load = () =>
    api.getAdminConfig().then((d) => { setCfg(d); setError(null) })
      .catch((e) => setError(String(e).replace('Error: ', '')))

  useEffect(() => { setLoading(true); load().finally(() => setLoading(false)) }, [])

  // After a section saves, the PUT returns the fresh effective config — swap it in.
  const onSaved = (fresh) => setCfg(fresh)

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted">
        <Loader2 size={15} className="animate-spin" /> Loading configuration…
      </div>
    )
  }
  if (error) {
    return <div className="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">{error}</div>
  }

  return (
    <div className="space-y-6">
      <div>
        <div className="mb-1 flex items-center gap-2">
          <SlidersHorizontal size={16} className="text-hutch-purple" />
          <h3 className="text-sm font-semibold text-content">Configuration</h3>
        </div>
        <p className="text-xs text-muted">
          Deployment settings, editable live — changes apply without restarting the backend.
          These override <code className="rounded bg-surface-3 px-1">backend/config.yml</code>,
          which remains the seed and the home of secrets, auth, and the vector store.
        </p>
      </div>

      <ProvidersCard cfg={cfg} onSaved={onSaved} />
      <PricingCard cfg={cfg} onSaved={onSaved} />
      <RuntimeCard cfg={cfg} onSaved={onSaved} />
      <SandboxCard cfg={cfg} onSaved={onSaved} />
    </div>
  )
}

// ---- shared bits ---------------------------------------------------------
function Card({ icon: Icon, title, desc, children }) {
  return (
    <div className="rounded-lg border border-border bg-surface p-4">
      <div className="mb-1 flex items-center gap-1.5">
        <Icon size={15} className="text-accent" />
        <h4 className="text-sm font-semibold text-content">{title}</h4>
      </div>
      {desc && <p className="mb-3 text-xs text-muted">{desc}</p>}
      {children}
    </div>
  )
}

function SaveButton({ onClick, busy, label = 'Save', disabled }) {
  return (
    <button onClick={onClick} disabled={busy || disabled}
      className="flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-sm text-accent-fg hover:opacity-90 disabled:opacity-50">
      {busy ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />} {label}
    </button>
  )
}

const inputCls =
  'w-full rounded-lg border-border bg-surface-2 text-sm text-content focus:border-accent focus:ring-accent'

// Wrap a section's save: surface errors inline, swap in the returned fresh config.
function useSaver(onSaved) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [ok, setOk] = useState(false)
  const save = async (section, body) => {
    setBusy(true); setErr(null); setOk(false)
    try {
      const fresh = await api.updateAdminConfig(section, body)
      onSaved(fresh)
      setOk(true); setTimeout(() => setOk(false), 2500)
    } catch (e) {
      setErr(String(e).replace('Error: ', ''))
    } finally {
      setBusy(false)
    }
  }
  return { busy, err, ok, save }
}

function Feedback({ err, ok }) {
  if (err) return <div className="mt-2 rounded bg-red-50 px-2 py-1 text-xs text-red-700">{err}</div>
  if (ok) return <div className="mt-2 inline-flex items-center gap-1 text-xs text-green-600"><Check size={12} /> Saved — applied live.</div>
  return null
}

// ---- Providers -----------------------------------------------------------
const BLANK_PROFILE = {
  name: '', type: 'openai', label: '', model: '', models: [], supports_tools: true,
  endpoint: '', api_key: '', aws_region: '',
}

function ProvidersCard({ cfg, onSaved }) {
  const [rows, setRows] = useState(() => cfg.providers.map(normalizeProfile))
  const { busy, err, ok, save } = useSaver(onSaved)
  const [testing, setTesting] = useState(null) // name -> result

  // Re-sync local edit state whenever a save returns fresh server config.
  useEffect(() => { setRows(cfg.providers.map(normalizeProfile)) }, [cfg.providers])

  const update = (i, patch) => setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)))
  const remove = (i) => setRows((rs) => rs.filter((_, j) => j !== i))
  const add = () => setRows((rs) => [...rs, { ...BLANK_PROFILE }])

  const submit = () => {
    const profiles = rows.map((r) => {
      const p = {
        name: r.name.trim(), type: r.type, label: r.label || null, model: r.model || null,
        models: (r.modelsText || '').split(',').map((s) => s.trim()).filter(Boolean),
        supports_tools: !!r.supports_tools,
      }
      if (r.type === 'openai') {
        p.endpoint = r.endpoint || null
        if (r.api_key) p.api_key = r.api_key            // only send when set/changed
      } else {
        p.aws_region = r.aws_region || null
        p.aws_profile = r.aws_profile || null
        // Secrets: only send when non-blank, so a blank field preserves the stored value.
        if (r.aws_access_key_id) p.aws_access_key_id = r.aws_access_key_id
        if (r.aws_secret_access_key) p.aws_secret_access_key = r.aws_secret_access_key
        if (r.aws_session_token) p.aws_session_token = r.aws_session_token
        if (r.aws_bedrock_api_key) p.aws_bedrock_api_key = r.aws_bedrock_api_key
      }
      return p
    })
    save('profiles', { profiles })
  }

  const test = async (name) => {
    setTesting({ name, pending: true })
    try {
      const r = await api.testProfile(name)
      setTesting({ name, ...r })
    } catch (e) {
      setTesting({ name, ok: false, error: String(e).replace('Error: ', '') })
    }
  }

  return (
    <Card icon={Cpu} title="Provider profiles"
      desc="Add, edit, or remove model providers. API keys are write-only — leave the key field blank to keep the existing one. Save first, then Test.">
      <div className="space-y-3">
        {rows.map((r, i) => (
          <div key={i} className="rounded-lg border border-border bg-surface-2 p-3">
            <div className="mb-2 flex items-center gap-2">
              <input placeholder="profile name (id)" value={r.name}
                onChange={(e) => update(i, { name: e.target.value })}
                className={`${inputCls} font-mono`} />
              <select value={r.type} onChange={(e) => update(i, { type: e.target.value })}
                className="rounded-lg border-border bg-surface-2 text-sm text-content focus:border-accent focus:ring-accent">
                <option value="openai">openai</option>
                <option value="bedrock">bedrock</option>
              </select>
              <button onClick={() => remove(i)} className="rounded p-1.5 text-muted hover:text-red-600" title="Remove profile">
                <Trash2 size={15} />
              </button>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <input placeholder="label (shown in UI)" value={r.label}
                onChange={(e) => update(i, { label: e.target.value })} className={inputCls} />
              <input placeholder="default model" value={r.model}
                onChange={(e) => update(i, { model: e.target.value })} className={`${inputCls} font-mono`} />
              {r.type === 'openai' && (
                <>
                  <input placeholder="endpoint (e.g. http://localhost:11434/v1)" value={r.endpoint}
                    onChange={(e) => update(i, { endpoint: e.target.value })} className={inputCls} />
                  <input type="password" autoComplete="off"
                    placeholder={r.api_key_set ? '••• set — leave blank to keep' : 'api key'}
                    value={r.api_key}
                    onChange={(e) => update(i, { api_key: e.target.value })} className={inputCls} />
                </>
              )}
              <input placeholder="models (comma-separated, optional)" value={r.modelsText}
                onChange={(e) => update(i, { modelsText: e.target.value })}
                className={`${inputCls} col-span-2 font-mono`} />
            </div>
            {r.type === 'bedrock' && <BedrockCreds r={r} onChange={(patch) => update(i, patch)} />}
            <div className="mt-2 flex items-center gap-3">
              <label className="flex items-center gap-1.5 text-xs text-muted">
                <input type="checkbox" checked={r.supports_tools}
                  onChange={(e) => update(i, { supports_tools: e.target.checked })}
                  className="rounded border-border text-accent focus:ring-accent" />
                supports tools
              </label>
              <button onClick={() => test(r.name)} disabled={!r.name}
                className="ml-auto flex items-center gap-1 rounded-lg border border-border px-2 py-1 text-xs text-content hover:border-accent disabled:opacity-50"
                title="Send a tiny prompt to verify the profile is reachable (uses the saved config)">
                <FlaskConical size={12} /> Test
              </button>
            </div>
            {testing?.name === r.name && (
              <div className="mt-1.5 text-xs">
                {testing.pending ? (
                  <span className="inline-flex items-center gap-1 text-muted"><Loader2 size={12} className="animate-spin" /> testing…</span>
                ) : testing.ok ? (
                  <span className="inline-flex items-center gap-1 text-green-600"><Check size={12} /> ok — {testing.model}</span>
                ) : (
                  <span className="inline-flex items-center gap-1 text-red-600"><X size={12} /> {testing.error}</span>
                )}
              </div>
            )}
          </div>
        ))}
      </div>
      <div className="mt-3 flex items-center gap-2">
        <button onClick={add} className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-content hover:border-accent">
          <Plus size={14} /> Add profile
        </button>
        <div className="flex-1" />
        <SaveButton onClick={submit} busy={busy} label="Save profiles" />
      </div>
      <Feedback err={err} ok={ok} />
    </Card>
  )
}

// AWS Bedrock has three ways to authenticate — surface all of them, since one field can't
// cover them. Secrets are write-only: a "••• set" placeholder means a value is stored and a
// blank field keeps it (matches the backend secret-preserve on save).
function BedrockCreds({ r, onChange }) {
  const secretPlaceholder = (isSet, label) => (isSet ? '••• set — blank keeps it' : label)
  return (
    <div className="mt-2 rounded-md border border-border bg-surface px-3 py-2">
      <div className="mb-2 text-[11px] font-medium text-muted">
        AWS credentials — fill <b>one</b> of: a Bedrock API key, explicit IAM keys, or leave all
        blank to use the host's AWS chain (env / <code>~/.aws</code> / instance role).
      </div>
      <div className="grid grid-cols-2 gap-2">
        <Field label="Region">
          <input placeholder="us-west-2" value={r.aws_region}
            onChange={(e) => onChange({ aws_region: e.target.value })} className={inputCls} />
        </Field>
        <Field label="Named profile (~/.aws), optional">
          <input placeholder="e.g. my-sso-profile" value={r.aws_profile || ''}
            onChange={(e) => onChange({ aws_profile: e.target.value })} className={inputCls} />
        </Field>
        <Field label="Bedrock API key (single bearer token)">
          <input type="password" autoComplete="off"
            placeholder={secretPlaceholder(r.aws_bedrock_api_key_set, 'bedrock API key')}
            value={r.aws_bedrock_api_key}
            onChange={(e) => onChange({ aws_bedrock_api_key: e.target.value })}
            className={`${inputCls} col-span-1`} />
        </Field>
        <div className="flex items-end text-[10px] text-muted">— or use IAM keys below —</div>
        <Field label="Access key ID">
          <input type="password" autoComplete="off"
            placeholder={secretPlaceholder(r.aws_access_key_id_set, 'AKIA… / ASIA…')}
            value={r.aws_access_key_id}
            onChange={(e) => onChange({ aws_access_key_id: e.target.value })} className={inputCls} />
        </Field>
        <Field label="Secret access key">
          <input type="password" autoComplete="off"
            placeholder={secretPlaceholder(r.aws_secret_access_key_set, 'secret access key')}
            value={r.aws_secret_access_key}
            onChange={(e) => onChange({ aws_secret_access_key: e.target.value })} className={inputCls} />
        </Field>
        <Field label="Session token (for temporary ASIA… keys)">
          <input type="password" autoComplete="off"
            placeholder={secretPlaceholder(r.aws_session_token_set, 'session token')}
            value={r.aws_session_token}
            onChange={(e) => onChange({ aws_session_token: e.target.value })}
            className={`${inputCls} col-span-2`} />
        </Field>
      </div>
    </div>
  )
}

function normalizeProfile(p) {
  return {
    ...p,
    label: p.label || '',
    model: p.model || '',
    endpoint: p.endpoint || '',
    aws_region: p.aws_region || '',
    aws_profile: p.aws_profile || '',
    // Secret inputs always start blank; *_set flags from the server drive the placeholders.
    api_key: '', aws_access_key_id: '',
    aws_secret_access_key: '', aws_session_token: '', aws_bedrock_api_key: '',
    modelsText: (p.models || []).join(', '),
    supports_tools: p.supports_tools !== false,
  }
}

// ---- Pricing -------------------------------------------------------------
function PricingCard({ cfg, onSaved }) {
  const [rows, setRows] = useState(() => pricingToRows(cfg.pricing))
  const { busy, err, ok, save } = useSaver(onSaved)
  useEffect(() => { setRows(pricingToRows(cfg.pricing)) }, [cfg.pricing])

  const update = (i, patch) => setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)))
  const remove = (i) => setRows((rs) => rs.filter((_, j) => j !== i))
  const add = () => setRows((rs) => [...rs, { model: '', input: '', output: '' }])

  const submit = () => {
    const pricing = {}
    for (const r of rows) {
      if (!r.model.trim()) continue
      pricing[r.model.trim()] = { input: Number(r.input) || 0, output: Number(r.output) || 0 }
    }
    save('pricing', { pricing })
  }

  return (
    <Card icon={DollarSign} title="Model pricing"
      desc="USD per 1,000,000 tokens, used for per-message cost and chargeback. Applies live to new turns.">
      <div className="space-y-2">
        <div className="grid grid-cols-[1fr_7rem_7rem_2rem] gap-2 text-[10px] uppercase tracking-wide text-muted">
          <div>Model id</div><div className="text-right">Input $/1M</div><div className="text-right">Output $/1M</div><div />
        </div>
        {rows.map((r, i) => (
          <div key={i} className="grid grid-cols-[1fr_7rem_7rem_2rem] items-center gap-2">
            <input placeholder="model id" value={r.model}
              onChange={(e) => update(i, { model: e.target.value })} className={`${inputCls} font-mono`} />
            <input type="number" step="0.01" min="0" value={r.input}
              onChange={(e) => update(i, { input: e.target.value })} className={`${inputCls} text-right`} />
            <input type="number" step="0.01" min="0" value={r.output}
              onChange={(e) => update(i, { output: e.target.value })} className={`${inputCls} text-right`} />
            <button onClick={() => remove(i)} className="rounded p-1.5 text-muted hover:text-red-600" title="Remove">
              <Trash2 size={14} />
            </button>
          </div>
        ))}
        {rows.length === 0 && <p className="text-xs text-muted">No pricing set — models will show tokens but $0.</p>}
      </div>
      <div className="mt-3 flex items-center gap-2">
        <button onClick={add} className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-content hover:border-accent">
          <Plus size={14} /> Add model
        </button>
        <div className="flex-1" />
        <SaveButton onClick={submit} busy={busy} label="Save pricing" />
      </div>
      <Feedback err={err} ok={ok} />
    </Card>
  )
}

function pricingToRows(pricing) {
  return Object.entries(pricing || {}).map(([model, rate]) => ({
    model, input: rate.input ?? '', output: rate.output ?? '',
  }))
}

// ---- Runtime (resilience + generation defaults) --------------------------
function RuntimeCard({ cfg, onSaved }) {
  const [res, setRes] = useState(cfg.resilience)
  const [gen, setGen] = useState(cfg.generation)
  const resSaver = useSaver(onSaved)
  const genSaver = useSaver(onSaved)
  useEffect(() => { setRes(cfg.resilience) }, [cfg.resilience])
  useEffect(() => { setGen(cfg.generation) }, [cfg.generation])

  const profileNames = cfg.providers.map((p) => p.name)
  const num = (v) => (v === '' || v == null ? null : Number(v))

  return (
    <Card icon={SlidersHorizontal} title="Runtime defaults"
      desc="Provider resilience and the deployment-wide generation defaults. Generation defaults seed new or unset users; they don't override a user's own saved settings.">
      <div className="grid grid-cols-2 gap-3">
        <Field label="Request timeout (s)">
          <input type="number" min="1" value={res.timeout ?? ''} className={inputCls}
            onChange={(e) => setRes({ ...res, timeout: num(e.target.value) })} />
        </Field>
        <Field label="Max retries">
          <input type="number" min="0" value={res.max_retries ?? ''} className={inputCls}
            onChange={(e) => setRes({ ...res, max_retries: num(e.target.value) })} />
        </Field>
        <Field label="Fallback profile (if active fails)">
          <select value={res.fallback_profile || ''} className={inputCls}
            onChange={(e) => setRes({ ...res, fallback_profile: e.target.value || null })}>
            <option value="">— none —</option>
            {profileNames.map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </Field>
      </div>
      <div className="mt-2 flex justify-end">
        <SaveButton onClick={() => resSaver.save('resilience', {
          timeout: res.timeout, max_retries: res.max_retries, fallback_profile: res.fallback_profile,
        })} busy={resSaver.busy} label="Save resilience" />
      </div>
      <Feedback err={resSaver.err} ok={resSaver.ok} />

      <div className="mt-4 border-t border-border pt-3">
        <div className="grid grid-cols-2 gap-3">
          <Field label="Temperature">
            <input type="number" step="0.05" min="0" max="2" value={gen.temperature ?? ''} className={inputCls}
              onChange={(e) => setGen({ ...gen, temperature: num(e.target.value) })} />
          </Field>
          <Field label="Max output tokens">
            <input type="number" min="1" value={gen.max_tokens ?? ''} className={inputCls}
              onChange={(e) => setGen({ ...gen, max_tokens: num(e.target.value) })} />
          </Field>
          <Field label="Max tool rounds">
            <input type="number" min="1" value={gen.max_tool_rounds ?? ''} className={inputCls}
              onChange={(e) => setGen({ ...gen, max_tool_rounds: num(e.target.value) })} />
          </Field>
          <Field label="Max context tokens">
            <input type="number" min="1" value={gen.max_context_tokens ?? ''} className={inputCls}
              onChange={(e) => setGen({ ...gen, max_context_tokens: num(e.target.value) })} />
          </Field>
        </div>
        <Field label="Default system prompt (seeds new users)">
          <textarea rows={4} value={gen.system_prompt ?? ''} className={`${inputCls} font-mono text-xs`}
            onChange={(e) => setGen({ ...gen, system_prompt: e.target.value })} />
        </Field>
        <div className="mt-2 flex justify-end">
          <SaveButton onClick={() => genSaver.save('generation', gen)} busy={genSaver.busy} label="Save defaults" />
        </div>
        <Feedback err={genSaver.err} ok={genSaver.ok} />
      </div>
    </Card>
  )
}

function Field({ label, children }) {
  return (
    <label className="block">
      <span className="mb-1 block text-[11px] font-medium text-muted">{label}</span>
      {children}
    </label>
  )
}

// ---- Sandbox limits ------------------------------------------------------
function SandboxCard({ cfg, onSaved }) {
  const [c, setC] = useState(cfg.sandbox.container)
  const { busy, err, ok, save } = useSaver(onSaved)
  useEffect(() => { setC(cfg.sandbox.container) }, [cfg.sandbox.container])
  const runner = cfg.sandbox.runner

  const submit = () => save('sandbox', {
    container: {
      memory: c.memory || null, cpus: c.cpus != null ? String(c.cpus) : null,
      pids_limit: c.pids_limit === '' || c.pids_limit == null ? null : Number(c.pids_limit),
      network: c.network || null, python_image: c.python_image || null,
      node_image: c.node_image || null, engine: c.engine || null,
    },
  })

  return (
    <Card icon={Box} title="Sandbox limits"
      desc="Resource caps for container code execution. The runner type is set in config.yml and not changed here.">
      <div className="mb-3 flex items-center gap-2 rounded-lg bg-surface-2 px-3 py-2 text-xs">
        <ShieldAlert size={14} className={runner === 'container' ? 'text-green-600' : 'text-muted'} />
        <span className="text-muted">Active runner:</span>
        <span className="font-mono text-content">{runner}</span>
        {runner !== 'container' && (
          <span className="text-muted">— limits below apply only when the runner is <code className="rounded bg-surface-3 px-1">container</code> (set in config.yml).</span>
        )}
      </div>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Memory (e.g. 512m)">
          <input value={c.memory ?? ''} className={inputCls} onChange={(e) => setC({ ...c, memory: e.target.value })} />
        </Field>
        <Field label="CPUs (e.g. 1.0)">
          <input value={c.cpus ?? ''} className={inputCls} onChange={(e) => setC({ ...c, cpus: e.target.value })} />
        </Field>
        <Field label="PID limit">
          <input type="number" min="1" value={c.pids_limit ?? ''} className={inputCls}
            onChange={(e) => setC({ ...c, pids_limit: e.target.value })} />
        </Field>
        <Field label="Network">
          <select value={c.network ?? 'none'} className={inputCls} onChange={(e) => setC({ ...c, network: e.target.value })}>
            <option value="none">none (most secure)</option>
            <option value="bridge">bridge (allow egress)</option>
          </select>
        </Field>
        <Field label="Python image">
          <input value={c.python_image ?? ''} className={`${inputCls} font-mono`} onChange={(e) => setC({ ...c, python_image: e.target.value })} />
        </Field>
        <Field label="Node image">
          <input value={c.node_image ?? ''} className={`${inputCls} font-mono`} onChange={(e) => setC({ ...c, node_image: e.target.value })} />
        </Field>
      </div>
      <div className="mt-3 flex justify-end">
        <SaveButton onClick={submit} busy={busy} label="Save sandbox limits" />
      </div>
      <Feedback err={err} ok={ok} />
    </Card>
  )
}
