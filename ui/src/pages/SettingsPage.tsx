import { useEffect, useRef, useState } from 'react'
import { FormSection } from '../components/FormField'
import { PageHeader } from '../components/PageHeader'
import { notify } from '../components/Toast'
import { get_settings, get_smtp_settings, restart_server, save_settings, save_smtp_settings, send_smtp_test } from '../data/api'
import type { Settings, SmtpEncryption, SmtpSettingsInput } from '../data/types'
import { format_proxy_addresses, parse_proxy_addresses } from '../lib/proxy_settings'
import { use_auth_user } from '../data/auth'
import './SettingsPage.css'

const server_address_help = String.raw`Rechnername oder IP-Adresse; „Server“ im Ordner-Dialog zeigt die Freigaben von \\<Adresse>`

export function SettingsPage() {
  const user = use_auth_user()
  const [server_address, set_server_address] = useState('')
  const [public_url, set_public_url] = useState('')
  const [builtin, set_builtin] = useState(true)
  const [trusted_text, set_trusted_text] = useState('')
  const [allowed_domains_text, set_allowed_domains_text] = useState('')
  const [smtp_host, set_smtp_host] = useState('')
  const [smtp_port, set_smtp_port] = useState(587)
  const [smtp_username, set_smtp_username] = useState('')
  const [smtp_encryption, set_smtp_encryption] = useState<SmtpEncryption>('starttls')
  const [smtp_from_address, set_smtp_from_address] = useState('')
  const [smtp_from_name, set_smtp_from_name] = useState('')
  const [smtp_password, set_smtp_password] = useState('')
  const [smtp_password_set, set_smtp_password_set] = useState(false)
  const [test_address, set_test_address] = useState('')
  const [sending_test, set_sending_test] = useState(false)
  const test_address_initialized = useRef(false)
  const [loading, set_loading] = useState(true)
  const [saving, set_saving] = useState(false)
  const disabled = loading || saving

  useEffect(() => {
    let active = true
    const load_settings = async () => {
      try {
        const [settings, smtp_settings] = await Promise.all([get_settings(), get_smtp_settings()])
        if (active) {
          set_server_address(settings.folder_dialog.server_address)
          set_public_url(settings.proxy.public_url)
          set_builtin(settings.proxy.builtin)
          set_trusted_text(format_proxy_addresses(settings.proxy.trusted_proxies))
          set_allowed_domains_text(format_proxy_addresses(settings.auth.allowed_domains))
          set_smtp_host(smtp_settings.host)
          set_smtp_port(smtp_settings.port)
          set_smtp_username(smtp_settings.username)
          set_smtp_encryption(smtp_settings.encryption)
          set_smtp_from_address(smtp_settings.from_address)
          set_smtp_from_name(smtp_settings.from_name)
          set_smtp_password_set(smtp_settings.password_set)
        }
      } catch {
        // The request already reports errors.
      } finally {
        if (active) {
          if (!test_address_initialized.current) {
            set_test_address(user?.email ?? '')
            test_address_initialized.current = true
          }
          set_loading(false)
        }
      }
    }
    void load_settings()
    return () => { active = false }
  }, [])

  const send_test_mail = async () => {
    set_sending_test(true)
    try {
      const response = await send_smtp_test(test_address.trim())
      notify(response.detail)
    } catch {
      // The API client already reports the error.
    } finally {
      set_sending_test(false)
    }
  }

  const restart = async () => {
    if (!window.confirm('Server jetzt neu starten? Laufende Jobs werden unterbrochen und neu verteilt.')) return
    try {
      await restart_server()
    } catch {
      // The API client already reports the error.
    }
  }

  const save = async () => {
    set_saving(true)
    try {
      const settings: Settings = await save_settings({
        folder_dialog: { server_address },
        proxy: { public_url, builtin, trusted_proxies: parse_proxy_addresses(trusted_text) },
        auth: { allowed_domains: parse_proxy_addresses(allowed_domains_text) },
      })
      const smtp_settings = await save_smtp_settings({
        host: smtp_host,
        port: smtp_port,
        username: smtp_username,
        encryption: smtp_encryption,
        from_address: smtp_from_address,
        from_name: smtp_from_name,
        password: smtp_password === '' ? null : smtp_password,
      } satisfies SmtpSettingsInput)
      set_server_address(settings.folder_dialog.server_address)
      set_public_url(settings.proxy.public_url)
      set_builtin(settings.proxy.builtin)
      set_trusted_text(format_proxy_addresses(settings.proxy.trusted_proxies))
      set_allowed_domains_text(format_proxy_addresses(settings.auth.allowed_domains))
      set_smtp_host(smtp_settings.host)
      set_smtp_port(smtp_settings.port)
      set_smtp_username(smtp_settings.username)
      set_smtp_encryption(smtp_settings.encryption)
      set_smtp_from_address(smtp_settings.from_address)
      set_smtp_from_name(smtp_settings.from_name)
      set_smtp_password('')
      set_smtp_password_set(smtp_settings.password_set)
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
            <FormSection title="Mailversand">
              <div className="form-field">
                <label htmlFor="settings-smtp-host" className="form-field-label">SMTP-Server</label>
                <div className="form-field-control">
                  <input
                    id="settings-smtp-host"
                    className="input"
                    type="text"
                    value={smtp_host}
                    placeholder="z. B. smtp.office365.com"
                    disabled={disabled}
                    onChange={event => set_smtp_host(event.target.value)}
                  />
                </div>
              </div>
              <div className="form-field">
                <label htmlFor="settings-smtp-port" className="form-field-label">Port</label>
                <div className="form-field-control">
                  <input
                    id="settings-smtp-port"
                    className="input"
                    type="number"
                    value={smtp_port}
                    disabled={disabled}
                    onChange={event => set_smtp_port(Number(event.target.value))}
                  />
                </div>
              </div>
              <div className="form-field">
                <label htmlFor="settings-smtp-username" className="form-field-label">Benutzername</label>
                <div className="form-field-control">
                  <input
                    id="settings-smtp-username"
                    className="input"
                    type="text"
                    value={smtp_username}
                    placeholder="leer = ohne Anmeldung"
                    disabled={disabled}
                    onChange={event => set_smtp_username(event.target.value)}
                  />
                </div>
              </div>
              <div className="form-field">
                <label htmlFor="settings-smtp-encryption" className="form-field-label">Verschlüsselung</label>
                <div className="form-field-control">
                  <select
                    id="settings-smtp-encryption"
                    className="input"
                    value={smtp_encryption}
                    disabled={disabled}
                    onChange={event => set_smtp_encryption(event.target.value as SmtpEncryption)}
                  >
                    <option value="none">Keine</option>
                    <option value="starttls">STARTTLS</option>
                    <option value="tls">SSL/TLS</option>
                  </select>
                </div>
              </div>
              <div className="form-field">
                <label htmlFor="settings-smtp-from-address" className="form-field-label">Absenderadresse</label>
                <div className="form-field-control">
                  <input
                    id="settings-smtp-from-address"
                    className="input"
                    type="text"
                    value={smtp_from_address}
                    placeholder="z. B. marathon@looks.film"
                    disabled={disabled}
                    onChange={event => set_smtp_from_address(event.target.value)}
                  />
                </div>
              </div>
              <div className="form-field">
                <label htmlFor="settings-smtp-from-name" className="form-field-label">Absendername</label>
                <div className="form-field-control">
                  <input
                    id="settings-smtp-from-name"
                    className="input"
                    type="text"
                    value={smtp_from_name}
                    disabled={disabled}
                    onChange={event => set_smtp_from_name(event.target.value)}
                  />
                </div>
              </div>
              <div className="form-field">
                <label htmlFor="settings-smtp-password" className="form-field-label">Passwort</label>
                <div className="form-field-control">
                  <input
                    id="settings-smtp-password"
                    className="input"
                    type="password"
                    autoComplete="new-password"
                    value={smtp_password}
                    placeholder={smtp_password_set ? 'Gespeichert – leer lassen, um es beizubehalten' : 'Nicht gesetzt'}
                    disabled={disabled}
                    onChange={event => set_smtp_password(event.target.value)}
                  />
                </div>
              </div>
              <div className="form-field">
                <label htmlFor="settings-smtp-test-address" className="form-field-label">Testmail an</label>
                <div className="form-field-control">
                  <input
                    id="settings-smtp-test-address"
                    className="input"
                    type="email"
                    value={test_address}
                    disabled={disabled}
                    onChange={event => set_test_address(event.target.value)}
                  />
                </div>
              </div>
              <div className="form-field">
                <div className="form-field-control">
                  <button
                    type="button"
                    className="button"
                    disabled={disabled || sending_test || !test_address.trim()}
                    onClick={() => { void send_test_mail() }}
                  >Testmail senden</button>
                  <p className="form-field-help">Verwendet die gespeicherten Einstellungen</p>
                </div>
              </div>
            </FormSection>
            <FormSection title="Server">
              <button type="button" className="button" onClick={() => { void restart() }} disabled={disabled}>Server neu starten</button>
            </FormSection>
          </div>
        </div>
      </div>
    </>
  )
}
