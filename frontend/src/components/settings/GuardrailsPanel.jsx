import { useEffect, useState } from 'react'
import { Check, Info, Loader2, Plus, Shield, Trash2 } from 'lucide-react'
import { api } from '../../api/client'

const ACTIONS = [
  { value: 'off', label: 'Off' },
  { value: 'redact', label: 'Redact' },
  { value: 'block', label: 'Block' },
]

const EMPTY_POLICY = {
  enabled: false,
  input_action: 'redact',
  output_action: 'off',
  redaction_text: '[REDACTED]',
  builtin: {},
  custom_patterns: [],
}

const DEFAULT_SAMPLE = 'Contact me at jane@example.com, call 206-555-2407, employee EMP-12345.'

// Small labeled stat card for the status footer strip.
function Stat({ label, value, accent }) {
  return (
    <div className="flex-1 rounded-lg border border-border bg-surface px-3 py-2">
      <div className="text-[10px] font-semibold uppercase tracking-wide text-muted">{label}</div>
      <div className={`text-sm font-medium ${accent ? 'text-accent' : 'text-content'}`}>{value}</div>
    </div>
  )
}

export default function GuardrailsPanel() {
  const [policy, setPolicy] = useState(EMPTY_POLICY)
  const [builtins, setBuiltins] = useState([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState(null)
  const [sample, setSample] = useState(DEFAULT_SAMPLE)
  const [previewDir, setPreviewDir] = useState('input')
  const [preview, setPreview] = useState(null)
  const [previewing, setPreviewing] = useState(false)

  useEffect(() => {
    api.getAdminConfig()
      .then((r) => {
        setPolicy({ ...EMPTY_POLICY, ...(r.guardrails || {}) })
        setBuiltins(r.guardrails_builtins || [])
      })
      .catch((e) => setError(String(e).replace('Error: ', '')))
      .finally(() => setLoading(false))
  }, [])

  const patch = (upd) => {
    setPolicy((p) => ({ ...p, ...upd }))
    setSaved(false)
  }

  // Custom-pattern rows are saved without UI-only fields; blank rows are dropped.
  const cleanPatterns = () =>
    policy.custom_patterns
      .filter((p) => p.name?.trim() || p.regex?.trim())
      .map((p) => ({
        name: (p.name || '').trim(),
        regex: p.regex || '',
        action: p.action || 'redact',
        replacement: p.replacement || '',
        enabled: p.enabled !== false,
      }))

  const save = async () => {
    setSaving(true)
    setError(null)
    try {
      const body = { ...policy, custom_patterns: cleanPatterns() }
      const r = await api.updateAdminConfig('guardrails', body)
      setPolicy({ ...EMPTY_POLICY, ...(r.guardrails || {}) })
      setSaved(true)
    } catch (e) {
      setError(String(e).replace('Error: ', ''))
    } finally {
      setSaving(false)
    }
  }

  const runPreview = async () => {
    setPreviewing(true)
    setError(null)
    try {
      const body = {
        text: sample,
        direction: previewDir,
        policy: { ...policy, custom_patterns: cleanPatterns() },
      }
      setPreview(await api.previewGuardrails(body))
    } catch (e) {
      setError(String(e).replace('Error: ', ''))
    } finally {
      setPreviewing(false)
    }
  }

  const setPattern = (i, upd) => {
    const next = policy.custom_patterns.slice()
    next[i] = { ...next[i], ...upd }
    patch({ custom_patterns: next })
  }

  const addPattern = () =>
    patch({
      custom_patterns: [
        ...policy.custom_patterns,
        { name: '', regex: '', action: 'redact', replacement: policy.redaction_text, enabled: true },
      ],
    })

  const removePattern = (i) =>
    patch({ custom_patterns: policy.custom_patterns.filter((_, idx) => idx !== i) })

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted">
        <Loader2 size={14} className="animate-spin" /> Loading…
      </div>
    )
  }

  const enabledCustom = policy.custom_patterns.filter((p) => p.enabled !== false && (p.regex || '').trim()).length
  const globalBadge = `global ${policy.input_action}/${policy.output_action}`

  const selectCls =
    'rounded-lg border-border bg-surface text-sm text-content focus:border-accent focus:ring-accent'
  const inputCls =
    'rounded-lg border-border bg-surface text-sm text-content focus:border-accent focus:ring-accent'

  return (
    <div>
      <h3 className="mb-1 flex items-center gap-2 text-sm font-semibold text-content">
        <Shield size={15} className="text-accent" /> Guardrails
      </h3>
      <p className="mb-4 text-xs text-muted">
        Configure built-in PII detection, redaction, and blocking policies. The policy is
        enforced everywhere models are called — interactive chat and the API gateway.
        Content is inspected in memory and not stored.
      </p>

      {error && <div className="mb-3 rounded bg-red-50 px-3 py-1.5 text-xs text-red-700">{error}</div>}

      {/* PII policy header card */}
      <div className="mb-4 rounded-lg border border-border bg-surface p-3">
        <div className="mb-3 flex items-center justify-between">
          <label className="flex items-center gap-2 text-sm font-medium text-content">
            <input
              type="checkbox"
              checked={policy.enabled}
              onChange={(e) => patch({ enabled: e.target.checked })}
              className="rounded border-border text-accent focus:ring-accent"
            />
            Enabled
          </label>
          <button
            onClick={save}
            disabled={saving}
            className="flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-sm text-accent-fg hover:opacity-90 disabled:opacity-50"
          >
            {saving ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
            {saved ? 'Saved' : 'Save policy'}
          </button>
        </div>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <label className="text-xs text-muted">
            Input action
            <select
              value={policy.input_action}
              onChange={(e) => patch({ input_action: e.target.value })}
              className={`mt-1 w-full ${selectCls}`}
            >
              {ACTIONS.map((a) => <option key={a.value} value={a.value}>{a.label}</option>)}
            </select>
          </label>
          <label className="text-xs text-muted">
            Output action
            <select
              value={policy.output_action}
              onChange={(e) => patch({ output_action: e.target.value })}
              className={`mt-1 w-full ${selectCls}`}
            >
              {ACTIONS.map((a) => <option key={a.value} value={a.value}>{a.label}</option>)}
            </select>
            <span className="mt-1 block text-[10px] leading-snug">
              Output block rejects streaming requests and blocks non-streaming responses
              after provider return.
            </span>
          </label>
          <label className="text-xs text-muted">
            Redaction text
            <input
              value={policy.redaction_text}
              onChange={(e) => patch({ redaction_text: e.target.value })}
              className={`mt-1 w-full font-mono ${inputCls}`}
            />
          </label>
        </div>
      </div>

      {/* Built-in patterns */}
      <div className="mb-4 rounded-lg border border-border bg-surface p-3">
        <div className="text-[10px] font-semibold uppercase tracking-wide text-muted">Built-in patterns</div>
        <p className="mb-2 text-xs text-muted">
          Email, phone, SSN, credit card, and API key detectors. Hover the info icons to
          see the active regex.
        </p>
        <div className="space-y-2">
          {builtins.map((b) => (
            <div key={b.id} className="flex items-center gap-2 rounded-lg border border-border bg-surface-2 px-3 py-2">
              <label className="flex flex-1 items-center gap-2 text-sm text-content">
                <input
                  type="checkbox"
                  checked={policy.builtin?.[b.id] !== false}
                  onChange={(e) => patch({ builtin: { ...policy.builtin, [b.id]: e.target.checked } })}
                  className="rounded border-border text-accent focus:ring-accent"
                />
                {b.label}
              </label>
              <span className="group relative flex items-center">
                <Info size={13} className="cursor-help text-muted" />
                <span className="pointer-events-none absolute right-0 top-5 z-20 hidden max-w-[420px] whitespace-pre-wrap break-all rounded-lg border border-border bg-surface px-2.5 py-1.5 font-mono text-[10px] text-content shadow-lg group-hover:block">
                  {b.regex}
                </span>
              </span>
              <code className="rounded bg-surface-3 px-1.5 py-0.5 font-mono text-[11px] text-content">{b.replacement}</code>
              <span className="rounded-full border border-border px-2 py-0.5 text-[10px] text-muted">{globalBadge}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Custom patterns */}
      <div className="mb-4 rounded-lg border border-border bg-surface p-3">
        <div className="mb-1 flex items-center justify-between">
          <div className="text-[10px] font-semibold uppercase tracking-wide text-muted">Custom patterns</div>
          <button
            onClick={addPattern}
            className="flex items-center gap-1 rounded-lg border border-border px-2 py-1 text-xs text-content hover:bg-surface-2"
          >
            <Plus size={12} /> Add pattern
          </button>
        </div>
        <p className="mb-2 text-xs text-muted">
          Add regular expressions (Python <code>re</code> syntax) for organization-specific
          identifiers, secrets, or data classes.
        </p>
        {policy.custom_patterns.length === 0 && (
          <div className="rounded-lg border border-dashed border-border px-3 py-3 text-center text-xs text-muted">
            No custom patterns. Add one to match organization-specific data.
          </div>
        )}
        <div className="space-y-2">
          {policy.custom_patterns.map((p, i) => (
            <div key={i} className="flex flex-wrap items-end gap-2 rounded-lg border border-border bg-surface-2 px-3 py-2">
              <label className="flex items-center gap-1.5 pb-1.5 text-xs text-muted">
                <input
                  type="checkbox"
                  checked={p.enabled !== false}
                  onChange={(e) => setPattern(i, { enabled: e.target.checked })}
                  className="rounded border-border text-accent focus:ring-accent"
                />
                Enabled
              </label>
              <label className="text-[10px] text-muted">
                Name
                <input
                  value={p.name || ''}
                  onChange={(e) => setPattern(i, { name: e.target.value })}
                  className={`mt-0.5 block w-24 ${inputCls}`}
                />
              </label>
              <label className="min-w-0 flex-1 text-[10px] text-muted">
                Regex
                <input
                  value={p.regex || ''}
                  onChange={(e) => setPattern(i, { regex: e.target.value })}
                  placeholder="\bMRN-\d{6}\b"
                  className={`mt-0.5 block w-full font-mono ${inputCls}`}
                />
              </label>
              <label className="text-[10px] text-muted">
                Action
                <select
                  value={p.action || 'redact'}
                  onChange={(e) => setPattern(i, { action: e.target.value })}
                  className={`mt-0.5 block ${selectCls}`}
                >
                  <option value="redact">Redact</option>
                  <option value="block">Block</option>
                </select>
              </label>
              <label className="text-[10px] text-muted">
                Replacement
                <input
                  value={p.replacement || ''}
                  onChange={(e) => setPattern(i, { replacement: e.target.value })}
                  className={`mt-0.5 block w-28 font-mono ${inputCls}`}
                />
              </label>
              <button
                onClick={() => removePattern(i)}
                className="mb-0.5 flex items-center gap-1 rounded-lg border border-red-300 px-2 py-1.5 text-xs text-red-600 hover:bg-red-50"
              >
                <Trash2 size={12} /> Remove
              </button>
            </div>
          ))}
        </div>
      </div>

      {/* Test patterns */}
      <div className="mb-4 rounded-lg border border-border bg-surface p-3">
        <div className="mb-1 flex items-center justify-between gap-2">
          <div>
            <div className="text-[10px] font-semibold uppercase tracking-wide text-muted">Test patterns</div>
            <p className="text-xs text-muted">
              Preview redaction or blocking against sample text. Uses your current
              (unsaved) edits; samples are sent only to the local admin API and are not stored.
            </p>
          </div>
          <div className="flex shrink-0 flex-col items-end gap-1.5">
            <select value={previewDir} onChange={(e) => setPreviewDir(e.target.value)} className={selectCls}>
              <option value="input">Input policy</option>
              <option value="output">Output policy</option>
            </select>
            <button
              onClick={runPreview}
              disabled={previewing || !sample.trim()}
              className="flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1 text-xs text-content hover:bg-surface-2 disabled:opacity-50"
            >
              {previewing ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />} Preview
            </button>
          </div>
        </div>
        <textarea
          value={sample}
          onChange={(e) => setSample(e.target.value)}
          rows={2}
          className={`mb-2 w-full ${inputCls}`}
        />
        <div className="rounded-lg border border-border bg-surface-2 px-3 py-2 text-sm">
          {preview === null ? (
            <span className="text-xs text-muted">Run preview to see what the provider or client would receive.</span>
          ) : (
            <>
              {preview.blocked && (
                <div className="mb-1 text-xs font-semibold text-red-600">
                  ⛔ Blocked — this {previewDir} would be rejected.
                </div>
              )}
              {!preview.active && (
                <div className="mb-1 text-xs text-muted">
                  Policy inactive for this direction (disabled, or action is “off”) — text passes through unchanged.
                </div>
              )}
              <div className="whitespace-pre-wrap break-words font-mono text-xs text-content">{preview.result}</div>
              {preview.matched?.length > 0 && (
                <div className="mt-1 text-[11px] text-muted">Matched: {preview.matched.join(', ')}</div>
              )}
            </>
          )}
        </div>
      </div>

      {/* Status footer */}
      <div className="flex flex-wrap gap-2">
        <Stat label="Policy" value={policy.enabled ? 'enabled' : 'disabled'} accent={policy.enabled} />
        <Stat label="Input" value={policy.input_action} />
        <Stat label="Output" value={policy.output_action} />
        <Stat label="Custom patterns" value={String(enabledCustom)} />
        <Stat label="Streaming block" value={policy.output_action === 'block' ? 'rejected' : 'allowed'} />
      </div>
    </div>
  )
}
