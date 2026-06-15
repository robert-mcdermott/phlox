import { useState } from 'react'
import { ShieldCheck, KeyRound, CheckCircle, XCircle, Loader2 } from 'lucide-react'
import { api } from '../../api/client'
import { useStore } from '../../store/useStore'

const ENTRA_SNIPPET = `auth:
  enabled: true
  jwt_secret: "<a long random 32+ byte secret>"
  entra:
    tenant_id: "<tenant-guid>"
    client_id: "<app-client-id>"
    client_secret: "<client-secret>"
    redirect_uri: "https://your-host/api/auth/entra/callback"`

function Status({ ok, children }) {
  return (
    <span className={`inline-flex items-center gap-1 text-xs ${ok ? 'text-green-600' : 'text-muted'}`}>
      {ok ? <CheckCircle size={13} /> : <XCircle size={13} />} {children}
    </span>
  )
}

export default function AuthSettingsPanel() {
  const me = useStore((s) => s.user)
  const authConfig = useStore((s) => s.authConfig) || {}
  const [pw, setPw] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState(null)

  const changeOwnPassword = async () => {
    if (!pw.trim()) return
    setBusy(true)
    try {
      await api.updateUser(me.id, { password: pw })
      setPw('')
      setNotice('Your password was updated.')
      setTimeout(() => setNotice(null), 3000)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h3 className="mb-1 flex items-center gap-1.5 text-sm font-semibold text-content">
          <ShieldCheck size={15} className="text-accent" /> Authentication
        </h3>
        <p className="mb-3 text-xs text-muted">How sign-in is configured for this deployment.</p>
        <div className="flex flex-wrap gap-4 rounded-lg border border-border bg-surface px-3 py-2">
          <Status ok={authConfig.enabled}>Login required</Status>
          <Status ok={authConfig.allow_registration}>Self-registration</Status>
          <Status ok={authConfig.entra_enabled}>Entra ID (SSO)</Status>
        </div>
        <p className="mt-2 text-[11px] text-muted">
          These are set in <code className="rounded bg-surface-3 px-1">backend/config.yml</code> under{' '}
          <code className="rounded bg-surface-3 px-1">auth:</code> and apply after a backend restart.
        </p>
      </div>

      <div>
        <h3 className="mb-1 text-sm font-semibold text-content">Single sign-on (Microsoft Entra ID)</h3>
        <p className="mb-2 text-xs text-muted">
          {authConfig.entra_enabled
            ? 'Entra SSO is configured — a "Sign in with Microsoft" button appears on the login screen.'
            : 'Entra SSO is not configured yet. To enable it for production, register an app in Entra ID and add this to config.yml:'}
        </p>
        {!authConfig.entra_enabled && (
          <pre className="overflow-x-auto rounded-lg bg-[#0d1117] p-3 text-xs text-gray-200">{ENTRA_SNIPPET}</pre>
        )}
        <ol className="mt-2 list-decimal space-y-0.5 pl-4 text-[11px] text-muted">
          <li>Register a web app in Entra ID; redirect URI = <code className="rounded bg-surface-3 px-1">…/api/auth/entra/callback</code>.</li>
          <li>Grant delegated scopes <code className="rounded bg-surface-3 px-1">openid profile email</code>; create a client secret.</li>
          <li>New SSO users get the <b>user</b> role — promote them in the Users tab.</li>
          <li>The local admin account stays as a break-glass login.</li>
        </ol>
      </div>

      {me?.auth_provider !== 'entra' && (
        <div>
          <h3 className="mb-1 flex items-center gap-1.5 text-sm font-semibold text-content">
            <KeyRound size={15} className="text-accent" /> Change my password
          </h3>
          {notice && <div className="mb-2 rounded-lg bg-green-50 px-3 py-1.5 text-xs text-green-700">{notice}</div>}
          <div className="flex gap-2">
            <input
              type="password"
              value={pw}
              onChange={(e) => setPw(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && changeOwnPassword()}
              placeholder="New password"
              className="flex-1 rounded-lg border-border bg-surface text-sm text-content focus:border-accent focus:ring-accent"
            />
            <button onClick={changeOwnPassword} disabled={busy || !pw.trim()}
              className="flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-sm text-accent-fg hover:opacity-90 disabled:opacity-50">
              {busy ? <Loader2 size={14} className="animate-spin" /> : <KeyRound size={14} />} Update
            </button>
          </div>
          {me?.username === 'admin' && (
            <p className="mt-1.5 text-[11px] text-amber-600">
              You're signed in as the default admin — change this password before sharing access.
            </p>
          )}
        </div>
      )}
    </div>
  )
}
