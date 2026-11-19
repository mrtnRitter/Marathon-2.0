import { useState } from 'react'
import { Route, Routes } from 'react-router-dom'
import { Sidebar } from '../components/Sidebar'
import { ToastHost } from '../components/Toast'
import { ConnectionOverlay } from '../components/ConnectionOverlay'
import { use_server_connected } from '../data/use_connection'
import { routes } from './routes'
import './App.css'

const collapsed_key = 'marathon.sidebar_collapsed'

export function App() {
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
