import { useEffect, useState, lazy, Suspense } from 'react'
import Sidebar from './components/layout/Sidebar'
import Header from './components/layout/Header'
import ChatPage from './pages/ChatPage'
import LoginScreen from './components/auth/LoginScreen'
import CanvasPanel from './components/canvas/CanvasPanel'
import { useStore } from './store/useStore'

// Lazy-loaded: the settings drawer pulls in the admin/user management UI and is only
// opened on demand, so keep it out of the initial bundle.
const SettingsDrawer = lazy(() => import('./components/settings/SettingsDrawer'))

export default function App() {
  const init = useStore((s) => s.init)
  const logout = useStore((s) => s.logout)
  const authReady = useStore((s) => s.authReady)
  const authConfig = useStore((s) => s.authConfig)
  const user = useStore((s) => s.user)
  const newConversation = useStore((s) => s.newConversation)
  const canvas = useStore((s) => s.canvas)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [settingsTab, setSettingsTab] = useState('providers')
  const [sidebarOpen, setSidebarOpen] = useState(true)

  useEffect(() => {
    init()
    const onUnauthorized = () => logout()
    window.addEventListener('phlox-unauthorized', onUnauthorized)
    return () => window.removeEventListener('phlox-unauthorized', onUnauthorized)
  }, [init, logout])

  // Keyboard shortcuts.
  useEffect(() => {
    const onKey = (e) => {
      const mod = e.metaKey || e.ctrlKey
      if (mod && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        newConversation()
      } else if (mod && e.key === '\\') {
        e.preventDefault()
        setSidebarOpen((v) => !v)
      } else if (e.key === 'Escape') {
        setSettingsOpen(false)
        useStore.getState().closeCanvas()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [newConversation])

  const openSettings = (tab = 'providers') => {
    setSettingsTab(tab)
    setSettingsOpen(true)
  }

  // Auth gate.
  if (!authReady) {
    return <div className="flex h-screen items-center justify-center bg-bg text-muted">Loading…</div>
  }
  if (authConfig?.enabled && !user) {
    return <LoginScreen />
  }

  return (
    <div className="flex h-screen overflow-hidden bg-bg text-content">
      {sidebarOpen && <Sidebar onOpenSettings={openSettings} />}
      <div className="flex flex-1 flex-col min-w-0">
        <Header
          onToggleSidebar={() => setSidebarOpen((v) => !v)}
          onOpenSettings={openSettings}
        />
        <ChatPage onOpenSettings={openSettings} />
      </div>
      {canvas && <CanvasPanel />}
      {settingsOpen && (
        <Suspense fallback={null}>
          <SettingsDrawer initialTab={settingsTab} onClose={() => setSettingsOpen(false)} />
        </Suspense>
      )}
    </div>
  )
}
