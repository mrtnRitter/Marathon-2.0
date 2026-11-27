import { useEffect, useRef, useState } from 'react'
import { acquire_edit_lock, release_edit_lock } from './api'
import type { EditLock, ObjectType } from './types'
import { create_edit_lock, type EditLockController, type EditLockGrant, type EditLockState } from '../lib/edit_lock'

const idle_state = (): EditLockState => ({ status: 'idle', token: null, expires_at: null, message: null, conflict: false })

function to_edit_lock_grant(lock: EditLock): EditLockGrant {
  return { token: lock.token, expires_at: lock.expires_at, heartbeat_s: lock.heartbeat_s }
}

export function use_edit_lock(
  object_type: ObjectType,
  object_id: string | null,
  active: boolean,
): { state: EditLockState; token: string | null; retry: () => void } {
  const [state, set_state] = useState<EditLockState>(idle_state)
  const controller_ref = useRef<EditLockController | null>(null)

  useEffect(() => {
    if (!active || !object_id) {
      controller_ref.current = null
      set_state(idle_state())
      return
    }
    const current_id = object_id
    const controller = create_edit_lock({
      acquire: async token => to_edit_lock_grant(await acquire_edit_lock(object_type, current_id, token ?? undefined)),
      release: token => release_edit_lock(object_type, current_id, token),
    }, set_state)
    controller_ref.current = controller
    const start_timer = globalThis.setTimeout(() => controller.start(), 0)
    return () => {
      globalThis.clearTimeout(start_timer)
      if (controller_ref.current === controller) controller_ref.current = null
      controller.stop()
    }
  }, [object_type, object_id, active])

  const retry = (): void => controller_ref.current?.start()
  return { state, token: state.token, retry }
}
