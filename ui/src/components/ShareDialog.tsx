import { useEffect, useState } from 'react'
import { SlidePanel } from './SlidePanel'
import { get_user_directory, get_workflow_shares, set_workflow_owner, set_workflow_shares } from '../data/api'
import type { Workflow, WorkflowShareAccess } from '../data/types'
import { use_auth_user } from '../data/auth'
import { use_data } from '../data/use_data'
import './ShareDialog.css'

interface Props {
  workflow: Workflow
  on_close: () => void
  on_owner_changed: () => void
}

export function ShareDialog({ workflow, on_close, on_owner_changed }: Props) {
  const { data, loading } = use_data(
    () => Promise.all([get_user_directory(), get_workflow_shares(workflow.id)]),
    [workflow.id],
  )
  const [filter, set_filter] = useState('')
  const [selected_access, set_selected_access] = useState<Record<string, WorkflowShareAccess | ''>>({})
  const [owner_id, set_owner_id] = useState(workflow.owner?.id ?? '')
  const [saving, set_saving] = useState(false)
  const is_admin = use_auth_user()?.role === 'admin'
  const directory = data?.[0] ?? []

  useEffect(() => {
    if (!data) return
    set_selected_access(Object.fromEntries(data[1].map(share => [share.user_id, share.access])))
  }, [data])

  const visible_users = directory.filter(user =>
    user.id !== owner_id && user.email.toLowerCase().includes(filter.trim().toLowerCase()),
  )

  const change_owner = (user_id: string) => {
    if (!is_admin || !user_id || user_id === owner_id || saving) return
    set_owner_id(user_id)
  }

  const save = async () => {
    if (!data || saving) return
    set_saving(true)
    try {
      const shares = directory.flatMap(user => {
        const access = user.id === owner_id ? '' : selected_access[user.id] ?? ''
        return access ? [{ user_id: user.id, access }] : []
      })
      await set_workflow_shares(workflow.id, shares)
      if (owner_id !== (workflow.owner?.id ?? '')) {
        await set_workflow_owner(workflow.id, owner_id)
        on_owner_changed()
      }
      on_close()
    } catch {
      // The API client already displays the error.
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
          <label htmlFor="share-workflow-owner" className="form-field-label">Eigentümer</label>
          <div className="form-field-control">
            <select id="share-workflow-owner" className="input" value={owner_id} disabled={loading || !data || saving} onChange={event => change_owner(event.target.value)}>
              {!owner_id && <option value="">Eigentümer auswählen</option>}
              {directory.map(user => <option key={user.id} value={user.id}>{user.email}</option>)}
            </select>
          </div>
        </div>
      )}
      <div className="form-field share-dialog-filter">
        <label htmlFor="share-workflow-filter" className="form-field-label">Nach E-Mail filtern</label>
        <div className="form-field-control">
          <input id="share-workflow-filter" className="input" type="search" value={filter} onChange={event => set_filter(event.target.value)} placeholder="E-Mail-Adresse" />
        </div>
      </div>
      {loading && <p className="muted">Nutzer werden geladen …</p>}
      {!loading && !data && <p className="muted">Nutzer konnten nicht geladen werden.</p>}
      {data && visible_users.length === 0 && <p className="muted">Keine passenden Nutzer.</p>}
      {data && visible_users.length > 0 && (
        <div className="share-dialog-users">
          {visible_users.map(user => (
            <div className="share-dialog-user" key={user.id}>
              <div className="share-dialog-user-info">
                <strong>{user.display_name || user.email}</strong>
                <span>{user.email}</span>
              </div>
              <select
                className="input"
                aria-label={`Zugriff für ${user.email}`}
                value={selected_access[user.id] ?? ''}
                disabled={saving}
                onChange={event => set_selected_access(current => ({ ...current, [user.id]: event.target.value as WorkflowShareAccess | '' }))}
              >
                <option value="">Privat (kein Zugriff)</option>
                <option value="view">Teilen zur Ansicht</option>
                <option value="run">Teilen zur Ausführung</option>
                <option value="edit">Teilen zur Bearbeitung und Ausführung</option>
              </select>
            </div>
          ))}
        </div>
      )}
    </SlidePanel>
  )
}
