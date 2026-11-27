import { useEffect, useState } from 'react'
import { get_access_events, get_access_shares, get_user_directory, set_access_owner, set_access_shares } from '../data/api'
import type { ObjectType, ShareAccess } from '../data/types'
import { use_auth_user } from '../data/auth'
import { use_data } from '../data/use_data'
import { share_options } from '../lib/access'
import { format_datetime } from '../lib/format'
import { build_shares, type ShareLevel } from '../lib/share_form'
import { SlidePanel } from './SlidePanel'
import './ShareDialog.css'

export function ShareDialog(props: { object_type: ObjectType; object_id: string; on_close: () => void; on_changed: () => void }) {
  const { object_type, object_id, on_close, on_changed } = props
  const { data, loading } = use_data(
    () => Promise.all([
      get_user_directory(),
      get_access_shares(object_type, object_id),
      get_access_events(object_type, object_id),
    ]),
    [object_type, object_id],
  )
  const [filter, set_filter] = useState('')
  const [enabled, set_enabled] = useState(false)
  const [all_access, set_all_access] = useState<ShareLevel | ''>('')
  const [user_access, set_user_access] = useState<Record<string, ShareLevel | ''>>({})
  const [owner_id, set_owner_id] = useState('')
  const [saving, set_saving] = useState(false)
  const is_admin = use_auth_user()?.role === 'admin'
  const directory = data?.[0] ?? []

  useEffect(() => {
    if (!data) return
    const shares = data[1].shares
    set_enabled(shares.length > 0)
    set_all_access(shares.find(share => share.user_id === null)?.access ?? '')
    set_user_access(Object.fromEntries(
      shares.flatMap(share => share.user_id === null ? [] : [[share.user_id, share.access]]),
    ))
    set_owner_id(data[1].owner?.id ?? '')
  }, [data])

  const visible_users = directory.filter(user =>
    user.id !== owner_id && user.email.toLowerCase().includes(filter.trim().toLowerCase()),
  )

  const save = async () => {
    if (!data || saving) return
    set_saving(true)
    try {
      const current_owner_id = data[1].owner?.id ?? null
      const next_owner_id = owner_id || null
      if (is_admin && next_owner_id !== current_owner_id) {
        await set_access_owner(object_type, object_id, next_owner_id)
      }
      await set_access_shares(
        object_type,
        object_id,
        build_shares(enabled, all_access, user_access, next_owner_id),
      )
      on_changed()
      on_close()
    } catch {
      // The API client already displays save errors.
    } finally {
      set_saving(false)
    }
  }

  return (
    <SlidePanel open title="Teilen" icon="users" on_close={on_close} body_class_name="share-dialog" actions={[
      { label: 'Abbrechen', icon: 'close', on_click: on_close },
      { label: 'Speichern', icon: 'save', primary: true, on_click: save, disabled: loading || !data || saving },
    ]}>
      {is_admin && (
        <div className="form-field share-dialog-owner">
          <label htmlFor="share-owner" className="form-field-label">Besitzer</label>
          <div className="form-field-control">
            <select id="share-owner" className="input" value={owner_id} disabled={loading || !data || saving} onChange={event => set_owner_id(event.target.value)}>
              <option value="">Admin</option>
              {directory.map(user => <option key={user.id} value={user.id}>{user.display_name || user.email}</option>)}
            </select>
          </div>
        </div>
      )}
      <label className="share-dialog-toggle">
        <input type="checkbox" role="switch" checked={enabled} disabled={loading || !data || saving} onChange={event => set_enabled(event.target.checked)} />
        Teilen
      </label>
      {enabled && <>
        <div className="form-field share-dialog-filter">
          <label htmlFor="share-filter" className="form-field-label">Nach E-Mail filtern</label>
          <div className="form-field-control">
            <input id="share-filter" className="input" type="search" value={filter} onChange={event => set_filter(event.target.value)} placeholder="E-Mail-Adresse" />
          </div>
        </div>
        <div className="share-dialog-users">
          <div className="share-dialog-user">
            <div className="share-dialog-user-info"><strong>Alle</strong></div>
            <select className="input" aria-label="Zugriff für Alle" value={all_access} disabled={saving} onChange={event => set_all_access(event.target.value as ShareAccess | '')}>
              {share_options.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
          </div>
          {visible_users.map(user => (
            <div className="share-dialog-user" key={user.id}>
              <div className="share-dialog-user-info">
                <strong>{user.display_name || user.email}</strong>
                <span>{user.email}</span>
              </div>
              <select
                className="input"
                aria-label={`Zugriff für ${user.email}`}
                value={user_access[user.id] ?? ''}
                disabled={saving}
                onChange={event => set_user_access(current => ({ ...current, [user.id]: event.target.value as ShareAccess | '' }))}
              >
                {share_options.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
              </select>
            </div>
          ))}
        </div>
      </>}
      <section className="share-dialog-history">
        <h3>Verlauf</h3>
        <table>
          <thead><tr><th>Zeit</th><th>Wer</th><th>Text</th></tr></thead>
          <tbody>
            {data && (data[2].length ? data[2].map((event, index) => (
              <tr key={`${event.ts}-${index}`}><td>{format_datetime(event.ts)}</td><td>{event.actor}</td><td>{event.text}</td></tr>
            )) : <tr><td colSpan={3}>Keine Einträge</td></tr>)}
          </tbody>
        </table>
      </section>
    </SlidePanel>
  )
}
