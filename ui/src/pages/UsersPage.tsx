import { useState } from 'react'
import { PageHeader } from '../components/PageHeader'
import { ApiError, delete_user, delete_user_session, delete_user_sessions, get_user_sessions, get_users, unlock_user, update_user_role } from '../data/api'
import { use_auth_user } from '../data/auth'
import type { UserRecord, UserRole, UserSession } from '../data/types'
import { use_data } from '../data/use_data'
import { format_datetime } from '../lib/format'
import './UsersPage.css'

function error_detail(error: unknown): string {
  return error instanceof ApiError ? error.detail : String(error)
}

export function UsersPage() {
  const current_user = use_auth_user()
  const { data: users, loading, error, reload } = use_data(get_users, [], { live: ['users'] })
  const [expanded_user_id, set_expanded_user_id] = useState<string | null>(null)
  const [sessions_by_user, set_sessions_by_user] = useState<Record<string, UserSession[]>>({})
  const [loading_sessions, set_loading_sessions] = useState<string | null>(null)
  const [busy_user_id, set_busy_user_id] = useState<string | null>(null)
  const [busy_session_id, set_busy_session_id] = useState<string | null>(null)
  const [action_error, set_action_error] = useState('')

  const load_sessions = async (user_id: string) => {
    set_loading_sessions(user_id)
    try {
      const sessions = await get_user_sessions(user_id)
      set_sessions_by_user(previous => ({ ...previous, [user_id]: sessions }))
    } catch (reason) {
      set_action_error(error_detail(reason))
    } finally {
      set_loading_sessions(current => current === user_id ? null : current)
    }
  }

  const toggle_sessions = (user_id: string) => {
    if (expanded_user_id === user_id) {
      set_expanded_user_id(null)
      return
    }
    set_expanded_user_id(user_id)
    if (!Object.hasOwn(sessions_by_user, user_id)) void load_sessions(user_id)
  }

  const perform_user_action = async (user_id: string, action: () => Promise<void>) => {
    set_busy_user_id(user_id)
    set_action_error('')
    try {
      await action()
      reload()
    } catch (reason) {
      set_action_error(error_detail(reason))
    } finally {
      set_busy_user_id(null)
    }
  }

  const remove_user = (user: UserRecord) => {
    if (!window.confirm(`Konto ${user.email} löschen? Workflows dieses Nutzers sind danach nur noch für Admins sichtbar.`)) return
    void perform_user_action(user.id, () => delete_user(user.id))
  }

  const remove_session = async (user_id: string, session_id: string) => {
    set_busy_session_id(session_id)
    set_action_error('')
    try {
      await delete_user_session(user_id, session_id)
      await load_sessions(user_id)
      reload()
    } catch (reason) {
      set_action_error(error_detail(reason))
    } finally {
      set_busy_session_id(null)
    }
  }

  const refresh_after_all_sessions = async (user_id: string) => {
    await delete_user_sessions(user_id)
    if (expanded_user_id === user_id) await load_sessions(user_id)
  }

  return (
    <>
      <PageHeader title="Nutzer" />
      <div className="page-content">
        {error && <p className="users-error" role="alert">{error}</p>}
        {action_error && <p className="users-error" role="alert">{action_error}</p>}
        <div className={`users-table-wrap${loading ? ' is-loading' : ''}`} aria-busy={loading}>
          <table className="users-table">
            <thead>
              <tr><th>E-Mail</th><th>Rolle</th><th>Letzte Anmeldung</th><th>Sitzungen</th><th>Status</th><th>Aktionen</th></tr>
            </thead>
            <tbody>
              {(users ?? []).map(user => {
                const is_current_user = user.id === current_user?.id
                const sessions = sessions_by_user[user.id]
                return (
                  <UserRows
                    key={user.id}
                    user={user}
                    is_current_user={is_current_user}
                    expanded={expanded_user_id === user.id}
                    sessions={sessions}
                    loading_sessions={loading_sessions === user.id}
                    busy={busy_user_id === user.id}
                    busy_session_id={busy_session_id}
                    on_role_change={role => { void perform_user_action(user.id, () => update_user_role(user.id, role)) }}
                    on_toggle_sessions={() => toggle_sessions(user.id)}
                    on_all_sign_out={() => { void perform_user_action(user.id, () => refresh_after_all_sessions(user.id)) }}
                    on_unlock={() => { void perform_user_action(user.id, () => unlock_user(user.id)) }}
                    on_delete={() => remove_user(user)}
                    on_remove_session={session_id => { void remove_session(user.id, session_id) }}
                  />
                )
              })}
            </tbody>
          </table>
          {!loading && (users?.length ?? 0) === 0 && <div className="users-empty">Keine Nutzer</div>}
        </div>
      </div>
    </>
  )
}

function UserRows({
  user,
  is_current_user,
  expanded,
  sessions,
  loading_sessions,
  busy,
  busy_session_id,
  on_role_change,
  on_toggle_sessions,
  on_all_sign_out,
  on_unlock,
  on_delete,
  on_remove_session,
}: {
  user: UserRecord
  is_current_user: boolean
  expanded: boolean
  sessions: UserSession[] | undefined
  loading_sessions: boolean
  busy: boolean
  busy_session_id: string | null
  on_role_change: (role: UserRole) => void
  on_toggle_sessions: () => void
  on_all_sign_out: () => void
  on_unlock: () => void
  on_delete: () => void
  on_remove_session: (session_id: string) => void
}) {
  return (
    <>
      <tr>
        <td>{user.email}</td>
        <td>
          <select className="input users-role-select" aria-label={`Rolle für ${user.email}`} value={user.role} disabled={user.protected || busy} onChange={event => on_role_change(event.target.value as UserRole)}>
            <option value="user">Nutzer</option>
            <option value="admin">Admin</option>
          </select>
          {user.protected && <span className="users-protected-note">In Konfiguration festgelegt</span>}
        </td>
        <td>{user.last_login_at ? format_datetime(user.last_login_at) : 'noch nie'}</td>
        <td><button type="button" className="users-count-button" aria-expanded={expanded} onClick={on_toggle_sessions}>{user.session_count}</button></td>
        <td>{user.locked_until ? `Gesperrt bis ${format_datetime(user.locked_until)}` : ''}</td>
        <td className="users-actions">
          <button type="button" className="button" disabled={busy || user.session_count === 0} onClick={on_all_sign_out}>Alle abmelden</button>
          {user.locked_until && <button type="button" className="button" disabled={busy} onClick={on_unlock}>Sperre aufheben</button>}
          <button type="button" className="button" disabled={busy || user.protected || is_current_user} onClick={on_delete}>Löschen</button>
        </td>
      </tr>
      {expanded && (
        <tr className="users-sessions-row">
          <td colSpan={6}>
            {loading_sessions ? <div className="users-sessions-empty">Lädt</div> : !sessions?.length ? <div className="users-sessions-empty">Keine Sitzungen</div> : (
              <table className="users-sessions-table">
                <thead><tr><th>Gerät</th><th>Angemeldet</th><th>Zuletzt aktiv</th><th>Läuft ab</th><th>Aktion</th></tr></thead>
                <tbody>
                  {sessions.map(session => (
                    <tr key={session.id}>
                      <td>{session.user_agent || '–'}{session.current ? ' (diese Sitzung)' : ''}</td>
                      <td>{format_datetime(session.created_at)}</td>
                      <td>{format_datetime(session.last_seen_at)}</td>
                      <td>{format_datetime(session.expires_at)}</td>
                      <td><button type="button" className="button" disabled={busy_session_id === session.id} onClick={() => on_remove_session(session.id)}>Abmelden</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </td>
        </tr>
      )}
    </>
  )
}
