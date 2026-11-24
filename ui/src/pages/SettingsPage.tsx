import { useEffect, useState } from 'react'
import { FormSection } from '../components/FormField'
import { PageHeader } from '../components/PageHeader'
import { notify } from '../components/Toast'
import { get_settings, save_settings } from '../data/api'
import type { Settings } from '../data/types'
import { format_proxy_addresses, parse_proxy_addresses } from '../lib/proxy_settings'
import './SettingsPage.css'

const server_address_help = String.raw`Rechnername oder IP-Adresse; „Server“ im Ordner-Dialog zeigt die Freigaben von \\<Adresse>`

export function SettingsPage() {
  const [server_address, set_server_address] = useState('')
  const [public_url, set_public_url] = useState('')
  const [builtin, set_builtin] = useState(true)
  const [trusted_text, set_trusted_text] = useState('')
  const [allowed_domains_text, set_allowed_domains_text] = useState('')
  const [loading, set_loading] = useState(true)
  const [saving, set_saving] = useState(false)
  const disabled = loading || saving

  useEffect(() => {
    let active = true
    const load_settings = async () => {
      try {
        const settings = await get_settings()
        if (active) {
          set_server_address(settings.folder_dialog.server_address)
          set_public_url(settings.proxy.public_url)
          set_builtin(settings.proxy.builtin)
          set_trusted_text(format_proxy_addresses(settings.proxy.trusted_proxies))
          set_allowed_domains_text(format_proxy_addresses(settings.auth.allowed_domains))
        }
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
      const settings: Settings = await save_settings({
        folder_dialog: { server_address },
        proxy: { public_url, builtin, trusted_proxies: parse_proxy_addresses(trusted_text) },
        auth: { allowed_domains: parse_proxy_addresses(allowed_domains_text) },
      })
      set_server_address(settings.folder_dialog.server_address)
      set_public_url(settings.proxy.public_url)
      set_builtin(settings.proxy.builtin)
      set_trusted_text(format_proxy_addresses(settings.proxy.trusted_proxies))
      set_allowed_domains_text(format_proxy_addresses(settings.auth.allowed_domains))
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
            <FormSection title="Proxy & HTTPS">
              <div className="form-field">
                <label htmlFor="settings-public-url" className="form-field-label">Öffentliche Adresse</label>
                <div className="form-field-control">
                  <input
                    id="settings-public-url"
                    className="input"
                    type="text"
                    value={public_url}
                    placeholder="https://laptop-martin-r"
                    disabled={disabled}
                    onChange={event => set_public_url(event.target.value)}
                  />
                  <div className="form-field-help">Adresse, unter der Marathon erreichbar ist (nur https://, ohne Pfad)</div>
                </div>
              </div>
              <div className="form-field">
                <label htmlFor="settings-proxy-builtin" className="form-field-label">Proxy verwenden</label>
                <div className="form-field-control">
                  <label className="form-switch">
                    <input
                      id="settings-proxy-builtin"
                      type="checkbox"
                      checked={!builtin}
                      disabled={disabled}
                      onChange={event => set_builtin(!event.target.checked)}
                    />
                    <span className="form-switch-track" />
                  </label>
                </div>
              </div>
              <div className="form-field">
                <label htmlFor="settings-trusted-proxies" className="form-field-label">Proxy</label>
                <div className="form-field-control">
                  <input
                    id="settings-trusted-proxies"
                    className="input"
                    type="text"
                    value={trusted_text}
                    placeholder="0.0.0.0"
                    disabled={disabled || builtin}
                    onChange={event => set_trusted_text(event.target.value)}
                  />
                </div>
              </div>
              <div className="form-field-help">Änderungen in diesem Abschnitt gelten nach einem Neustart des Servers.</div>
            </FormSection>
            <FormSection title="Anmeldung">
              <div className="form-field">
                <label htmlFor="settings-allowed-domains" className="form-field-label">Erlaubte E-Mail-Domains</label>
                <div className="form-field-control">
                  <input
                    id="settings-allowed-domains"
                    className="input"
                    type="text"
                    value={allowed_domains_text}
                    placeholder="z. B. looks.film"
                    disabled={disabled}
                    onChange={event => set_allowed_domains_text(event.target.value)}
                  />
                </div>
              </div>
            </FormSection>
          </div>
        </div>
      </div>
    </>
  )
}
