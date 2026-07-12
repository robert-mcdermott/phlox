import { useState } from 'react'
import { KeyRound, Loader2, LogOut } from 'lucide-react'
import { useStore } from '../../store/useStore'

function errorMessage(error) {
  const raw = String(error).replace(/^Error: \d+: /, '')
  try {
    return JSON.parse(raw).detail || raw
  } catch {
    return raw
  }
}

export default function PasswordChangeScreen() {
  const user = useStore((s) => s.user)
  const changePassword = useStore((s) => s.changePassword)
  const logout = useStore((s) => s.logout)
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const submit = async (event) => {
    event.preventDefault()
    setError(null)
    if (newPassword.length < 12) {
      setError('Choose a password with at least 12 characters.')
      return
    }
    if (newPassword !== confirmPassword) {
      setError('The new passwords do not match.')
      return
    }
    setBusy(true)
    try {
      await changePassword(currentPassword, newPassword)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex h-screen items-center justify-center bg-bg px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex flex-col items-center text-center">
          <img src="/phlox-logo.svg" alt="Phlox" className="mb-3 h-12" />
          <h1 className="text-xl font-semibold text-content">Choose a new password</h1>
          <p className="mt-1 text-sm text-muted">
            The temporary password for <b>{user?.username}</b> must be replaced before
            you can continue.
          </p>
        </div>

        <form onSubmit={submit} className="space-y-3 rounded-2xl border border-border bg-surface p-6 shadow-sm">
          {error && <div className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}
          <label className="block">
            <span className="mb-1 block text-xs font-medium text-muted">Temporary password</span>
            <input
              type="password"
              value={currentPassword}
              onChange={(event) => setCurrentPassword(event.target.value)}
              autoFocus
              autoComplete="current-password"
              className="w-full rounded-lg border-border bg-surface-2 text-content focus:border-accent focus:ring-accent"
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-xs font-medium text-muted">New password</span>
            <input
              type="password"
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
              autoComplete="new-password"
              className="w-full rounded-lg border-border bg-surface-2 text-content focus:border-accent focus:ring-accent"
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-xs font-medium text-muted">Confirm new password</span>
            <input
              type="password"
              value={confirmPassword}
              onChange={(event) => setConfirmPassword(event.target.value)}
              autoComplete="new-password"
              className="w-full rounded-lg border-border bg-surface-2 text-content focus:border-accent focus:ring-accent"
            />
          </label>
          <p className="text-xs text-muted">Use at least 12 characters and store it in a password manager.</p>
          <button
            type="submit"
            disabled={busy || !currentPassword || !newPassword || !confirmPassword}
            className="flex w-full items-center justify-center gap-2 rounded-lg bg-accent py-2.5 text-sm font-medium text-accent-fg hover:opacity-90 disabled:opacity-50"
          >
            {busy ? <Loader2 size={16} className="animate-spin" /> : <KeyRound size={16} />}
            Set password and continue
          </button>
          <button
            type="button"
            onClick={logout}
            className="flex w-full items-center justify-center gap-2 py-1 text-xs text-muted hover:text-content"
          >
            <LogOut size={14} /> Sign out
          </button>
        </form>
      </div>
    </div>
  )
}
