import { useState } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { Sidebar } from '../components/Sidebar'
import { ToastHost } from '../components/Toast'
import { ConnectionOverlay } from '../components/ConnectionOverlay'
import { use_server_connected } from '../data/use_connection'
import { routes } from './routes'
import { AuthProvider, use_auth_user } from '../data/auth'
import { LoginPage } from '../pages/LoginPage'
import './App.css'

const collapsed_key = 'marathon.sidebar_collapsed'

export function App() {
  return <AuthProvider><AppContent /></AuthProvider>
}

function AppContent() {
  const user = use_auth_user()
  const location = useLocation()

  if (user === undefined) return <div className="loading" role="status">Lädt</div>
  if (location.pathname === '/login') return user ? <Navigate to="/" replace /> : <LoginPage />
  if (!user) {
    const next = `${location.pathname}${location.search}`
    return <Navigate to={`/login?next=${encodeURIComponent(next)}`} replace />
  }
  return <AuthenticatedApp />
}

function AuthenticatedApp() {
  const connected = use_server_connected()
  const [collapsed, set_collapsed] = useState(() => localStorage.getItem(collapsed_key) === '1')
  const toggle = () => set_collapsed(value => {
    localStorage.setItem(collapsed_key, value ? '0' : '1')
    return !value
  })
  return (
    <>
      <div className="app" inert={!connected}>
        <Sidebar collapsed={collapsed} on_toggle={toggle} />
        <main className="app-main">
          <Routes>
            {routes.map(route => <Route key={route.path} path={route.path} element={route.element} />)}
          </Routes>
        </main>
        <ToastHost />
      </div>
      <ConnectionOverlay />
    </>
  )
}
