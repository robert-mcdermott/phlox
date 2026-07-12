import { useEffect, useState } from 'react'
import { LogIn, Loader2, UserPlus } from 'lucide-react'
import { useStore } from '../../store/useStore'
import { api } from '../../api/client'

export default function LoginScreen() {
  const authConfig = useStore((s) => s.authConfig)
  const login = useStore((s) => s.login)
  const registerAccount = useStore((s) => s.registerAccount)
  const completeEntraLogin = useStore((s) => s.completeEntraLogin)
  const [mode, setMode] = useState('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    const params = new URLSearchParams(window.location.hash.replace(/^#/, ''))
    const handoff = params.get('sso_handoff')
    const ssoError = params.get('sso_error')
    if (!handoff && !ssoError) return
    window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}`)
    if (ssoError) {
      setError('Microsoft sign-in could not be completed. Please try again.')
      return
    }
    setBusy(true)
    completeEntraLogin(handoff)
      .catch(() => setError('Microsoft sign-in expired or could not be completed. Please try again.'))
      .finally(() => setBusy(false))
  }, [completeEntraLogin])

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      if (mode === 'login') await login(username.trim(), password)
      else await registerAccount({ username: username.trim(), password })
    } catch (err) {
      setError(mode === 'login' ? 'Invalid username or password.' : String(err).replace('Error: ', ''))
    } finally {
      setBusy(false)
    }
  }

  const entraLogin = async () => {
    try {
      const { authorize_url } = await api.entraLoginUrl()
      window.location.href = authorize_url
    } catch {
      setError('Entra SSO is not available.')
    }
  }

  return (
    <div className="flex h-screen items-center justify-center bg-bg px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex flex-col items-center">
          <img src="/phlox-logo.svg" alt="Phlox" className="mb-3 h-12" />
          <h1 className="text-xl font-semibold text-content">Phlox</h1>
          <p className="text-sm text-muted">{mode === 'login' ? 'Sign in to continue' : 'Create an account'}</p>
        </div>

        <form onSubmit={submit} className="space-y-3 rounded-2xl border border-border bg-surface p-6 shadow-sm">
          {error && <div className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}
          <label className="block">
            <span className="mb-1 block text-xs font-medium text-muted">Username</span>
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoFocus
              className="w-full rounded-lg border-border bg-surface-2 text-content focus:border-accent focus:ring-accent"
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-xs font-medium text-muted">Password</span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full rounded-lg border-border bg-surface-2 text-content focus:border-accent focus:ring-accent"
            />
          </label>
          <button
            type="submit"
            disabled={busy || !username || !password}
            className="flex w-full items-center justify-center gap-2 rounded-lg bg-accent py-2.5 text-sm font-medium text-accent-fg hover:opacity-90 disabled:opacity-50"
          >
            {busy ? <Loader2 size={16} className="animate-spin" /> : mode === 'login' ? <LogIn size={16} /> : <UserPlus size={16} />}
            {mode === 'login' ? 'Sign in' : 'Create account'}
          </button>

          {authConfig?.entra_enabled && (
            <button
              type="button"
              onClick={entraLogin}
              className="w-full rounded-lg border border-border py-2.5 text-sm text-content hover:border-accent"
            >
              Sign in with Microsoft (Entra ID)
            </button>
          )}

          {authConfig?.allow_registration && (
            <button
              type="button"
              onClick={() => { setMode(mode === 'login' ? 'register' : 'login'); setError(null) }}
              className="w-full text-center text-xs text-muted hover:text-accent"
            >
              {mode === 'login' ? "No account? Register" : 'Have an account? Sign in'}
            </button>
          )}
        </form>
      </div>
    </div>
  )
}
