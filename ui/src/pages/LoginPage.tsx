import { useState, type FormEvent } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { ApiError, confirm_admin_code, confirm_auth_code, request_auth_code } from '../data/api'
import { use_auth_actions } from '../data/auth'
import type { AuthUser } from '../data/types'
import './LoginPage.css'

function get_error_detail(error: unknown): string {
  return error instanceof ApiError ? error.detail : String(error)
}

function login_destination(search: string): string {
  const next = new URLSearchParams(search).get('next')
  if (!next?.startsWith('/') || next.startsWith('//')) return '/'
  try {
    return new URL(next, window.location.origin).origin === window.location.origin ? next : '/'
  } catch {
    return '/'
  }
}

export function LoginPage() {
  const location = useLocation()
  const navigate = useNavigate()
  const { set_authenticated_user } = use_auth_actions()
  const [view, set_view] = useState<'email' | 'admin'>('email')
  const [email, set_email] = useState('')
  const [code, set_code] = useState('')
  const [sent_detail, set_sent_detail] = useState('')
  const [error, set_error] = useState('')
  const [submitting, set_submitting] = useState(false)

  const send_code = async (event?: FormEvent) => {
    event?.preventDefault()
    set_error('')
    set_submitting(true)
    try {
      const response = await request_auth_code(email)
      set_sent_detail(response.detail)
      set_code('')
    } catch (reason) {
      set_error(get_error_detail(reason))
    } finally {
      set_submitting(false)
    }
  }

  const complete_sign_in = (user: AuthUser) => {
    set_authenticated_user(user)
    navigate(login_destination(location.search), { replace: true })
  }

  const sign_in = async (event: FormEvent, confirm: () => Promise<AuthUser>) => {
    event.preventDefault()
    if (!/^\d{6}$/.test(code)) return
    set_error('')
    set_submitting(true)
    try {
      complete_sign_in(await confirm())
    } catch (reason) {
      set_error(get_error_detail(reason))
    } finally {
      set_submitting(false)
    }
  }

  const switch_view = (next_view: 'email' | 'admin') => {
    set_view(next_view)
    set_error('')
    set_code('')
  }

  return (
    <main className="login-page">
      <section className="login-card panel">
        <h1>Bei Marathon anmelden</h1>
        {view === 'email' ? (
          <>
            {!sent_detail ? (
              <form className="login-form" onSubmit={event => { void send_code(event) }}>
                <label htmlFor="login-email">E-Mail-Adresse</label>
                <input
                  id="login-email"
                  className="input"
                  type="email"
                  autoComplete="email"
                  required
                  value={email}
                  disabled={submitting}
                  onChange={event => set_email(event.target.value)}
                />
                <button className="button button-primary" type="submit" disabled={submitting || !email.trim()}>Code senden</button>
              </form>
            ) : (
              <>
                <p className="login-detail">{sent_detail}</p>
                <form className="login-form" onSubmit={event => { void sign_in(event, () => confirm_auth_code(email, code)) }}>
                  <label htmlFor="login-code">Code</label>
                  <input
                    id="login-code"
                    className="input login-code-input"
                    type="text"
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    maxLength={6}
                    placeholder="123456"
                    value={code}
                    disabled={submitting}
                    onChange={event => set_code(event.target.value.replace(/\D/g, '').slice(0, 6))}
                  />
                  <button className="button button-primary" type="submit" disabled={submitting || !/^\d{6}$/.test(code)}>Anmelden</button>
                </form>
                <div className="login-links">
                  <button type="button" className="login-link" disabled={submitting} onClick={() => { void send_code() }}>Neuen Code senden</button>
                  <button type="button" className="login-link" disabled={submitting} onClick={() => { set_sent_detail(''); set_code(''); set_error('') }}>Andere Adresse</button>
                </div>
              </>
            )}
            {error && <p className="login-error" role="alert">{error}</p>}
            <div className="login-links">
              <button type="button" className="login-link" disabled={submitting} onClick={() => switch_view('admin')}>Mit Admin-Code anmelden</button>
            </div>
          </>
        ) : (
          <>
            <form className="login-form" onSubmit={event => { void sign_in(event, () => confirm_admin_code(code)) }}>
              <label htmlFor="login-admin-code">Admin-Code</label>
              <input
                id="login-admin-code"
                className="input login-code-input"
                type="text"
                inputMode="numeric"
                autoComplete="one-time-code"
                maxLength={6}
                placeholder="123456"
                value={code}
                disabled={submitting}
                onChange={event => set_code(event.target.value.replace(/\D/g, '').slice(0, 6))}
              />
              <button className="button button-primary" type="submit" disabled={submitting || !/^\d{6}$/.test(code)}>Anmelden</button>
            </form>
            {error && <p className="login-error" role="alert">{error}</p>}
            <div className="login-links">
              <button type="button" className="login-link" disabled={submitting} onClick={() => switch_view('email')}>Zurück zur Anmeldung per E-Mail</button>
            </div>
          </>
        )}
      </section>
    </main>
  )
}
