import { useEffect, useState } from 'react'
import { FormSection } from '../components/FormField'
import { PageHeader } from '../components/PageHeader'
import { notify } from '../components/Toast'
import { get_settings, save_settings } from '../data/api'
import type { Settings } from '../data/types'
import './SettingsPage.css'

const server_address_help = String.raw`Rechnername oder IP-Adresse; „Server“ im Ordner-Dialog zeigt die Freigaben von \\<Adresse>`

export function SettingsPage() {
  const [server_address, set_server_address] = useState('')
  const [loading, set_loading] = useState(true)
  const [saving, set_saving] = useState(false)
  const disabled = loading || saving

  useEffect(() => {
    let active = true
    const load_settings = async () => {
      try {
        const settings = await get_settings()
        if (active) set_server_address(settings.folder_dialog.server_address)
      } catch {
        // The request already reports errors.
      } finally {
        if (active) set_loading(false)
      }
    }
    void load_settings()
    return () => { active = false }
  }, [])

  const save = async () => {
    set_saving(true)
    try {
      const settings: Settings = await save_settings({ folder_dialog: { server_address } })
      set_server_address(settings.folder_dialog.server_address)
      notify('Gespeichert')
    } catch {
      // The request already reports errors.
    } finally {
      set_saving(false)
    }
  }

  return (
    <>
      <PageHeader title="Einstellungen" actions={[{ label: 'Speichern', icon: 'save', primary: true, on_click: () => { void save() }, disabled }]} />
      <div className="page-content">
        <div className="panel settings-panel" aria-busy={disabled}>
          <div className="panel-body">
            <FormSection title="Ordner-Dialog">
              <div className="form-field">
                <label htmlFor="settings-server-address" className="form-field-label">Server-Adresse</label>
                <div className="form-field-control">
                  <input
                    id="settings-server-address"
                    className="input"
                    type="text"
                    value={server_address}
                    placeholder="z. B. 10.0.77.11"
                    disabled={disabled}
                    onChange={event => set_server_address(event.target.value)}
                  />
                  <div className="form-field-help">{server_address_help}</div>
                </div>
              </div>
            </FormSection>
          </div>
        </div>
      </div>
    </>
  )
}
