import type { JSX } from 'react'
import { X } from 'lucide-react'
import { use_server_connected } from '../data/use_connection'
import './ConnectionOverlay.css'

export function ConnectionOverlay(): JSX.Element | null {
  const connected = use_server_connected()
  if (connected) return null
  return (
    <div className="connection-overlay" role="alertdialog" aria-modal="true" aria-label="Keine Verbindung zum Marathon-Server">
      <div className="connection-overlay-box">
        <strong>Keine Verbindung zum Marathon-Server</strong>
        <p>Die Seite verbindet sich automatisch neu.</p>
      </div>
      <span className="connection-overlay-status" aria-hidden="true"><X size={24} strokeWidth={2.5} /></span>
    </div>
  )
}
