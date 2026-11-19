import type { JSX } from 'react'
import { Icon } from './Icon'
import { use_server_connected } from '../data/use_connection'
import './ConnectionOverlay.css'

export function ConnectionOverlay(): JSX.Element | null {
  const connected = use_server_connected()
  if (connected) return null
  return (
    <div className="connection-overlay" role="alertdialog" aria-modal="true" aria-label="Keine Verbindung zum Marathon-Server">
      <div className="connection-overlay-box">
        <Icon name="server" size={32} />
        <strong>Keine Verbindung zum Marathon-Server</strong>
        <p>Die Seite verbindet sich automatisch neu.</p>
      </div>
    </div>
  )
}
