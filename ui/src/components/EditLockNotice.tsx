import { useState } from 'react'
import { use_auth_user } from '../data/auth'
import { break_edit_lock } from '../data/api'
import type { ObjectType } from '../data/types'
import { lock_holder_name, type EditLockState } from '../lib/edit_lock'
import './EditLockNotice.css'

export function EditLockNotice(props: { object_type: ObjectType; object_id: string; state: EditLockState; on_retry: () => void }) {
  const { object_type, object_id, state, on_retry } = props
  const [breaking, set_breaking] = useState(false)
  const is_admin = use_auth_user()?.role === 'admin'

  if (state.status === 'idle' || state.status === 'acquiring' || state.status === 'held') return null

  const holder_name = lock_holder_name(state.message)
  const message = state.message ?? ''

  async function clear_lock(): Promise<void> {
    if (breaking) return
    const name = holder_name ?? 'einem anderen Nutzer'
    if (!window.confirm(`Bearbeitungssperre von ${name} aufheben? Nicht gespeicherte Änderungen gehen dort verloren.`)) return
    set_breaking(true)
    try {
      await break_edit_lock(object_type, object_id)
      on_retry()
    } catch {
      // The API client already displays errors.
    } finally {
      set_breaking(false)
    }
  }

  return (
    <div className="edit-lock-notice" role="alert">
      <span>{message}</span>
      {is_admin && state.conflict && (
        <button type="button" disabled={breaking} onClick={() => { void clear_lock() }}>Sperre aufheben</button>
      )}
    </div>
  )
}
