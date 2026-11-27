export type EditLockStatus = 'idle' | 'acquiring' | 'held' | 'denied' | 'lost'

export interface EditLockState {
  status: EditLockStatus
  token: string | null
  expires_at: string | null
  message: string | null
  conflict: boolean
}

export interface EditLockGrant { token: string; expires_at: string; heartbeat_s: number }
export interface EditLockApi { acquire(token: string | null): Promise<EditLockGrant>; release(token: string): Promise<unknown> }
export interface EditLockTimers { set_timeout(callback: () => void, ms: number): unknown; clear_timeout(handle: unknown): void }
export interface EditLockOptions { timers?: EditLockTimers; request_timeout_ms?: number; max_failures?: number }
export interface EditLockController { start(): void; stop(): void; state(): EditLockState }

const idle_state = (): EditLockState => ({ status: 'idle', token: null, expires_at: null, message: null, conflict: false })
const default_timers: EditLockTimers = {
  set_timeout: (callback, ms) => globalThis.setTimeout(callback, ms),
  clear_timeout: handle => globalThis.clearTimeout(handle as ReturnType<typeof globalThis.setTimeout>),
}

interface LockRequest {
  generation: number
  id: number
  phase: 'initial' | 'heartbeat'
  timed_out: boolean
}

export function create_edit_lock(
  api: EditLockApi,
  on_change: (state: EditLockState) => void,
  options: EditLockOptions = {},
): EditLockController {
  const timers = options.timers ?? default_timers
  const request_timeout_ms = options.request_timeout_ms ?? 10000
  const max_failures = options.max_failures ?? 3
  let current_state = idle_state()
  let generation = 0
  let request_id = 0
  let latest_request_id = 0
  let active_request_id: number | null = null
  let consecutive_failures = 0
  let remembered_token: string | null = null
  let heartbeat_ms = 0
  let timer_handle: unknown
  let has_timer = false

  function publish(next: EditLockState): void {
    const changed = current_state.status !== next.status
      || current_state.token !== next.token
      || current_state.expires_at !== next.expires_at
      || current_state.message !== next.message
      || current_state.conflict !== next.conflict
    if (!changed) return
    current_state = { ...next }
    on_change({ ...current_state })
  }

  function clear_timer(): void {
    if (!has_timer) return
    timers.clear_timeout(timer_handle)
    has_timer = false
    timer_handle = undefined
  }

  function set_timer(callback: () => void, ms: number): void {
    clear_timer()
    has_timer = true
    timer_handle = timers.set_timeout(() => {
      has_timer = false
      timer_handle = undefined
      callback()
    }, ms)
  }

  function release_without_wait(token: string): void {
    try {
      void Promise.resolve(api.release(token)).catch(() => {})
    } catch {}
  }

  function error_details(error: unknown): { message: string; conflict: boolean } {
    const message = error instanceof Error ? error.message : 'Server nicht erreichbar'
    const conflict = typeof error === 'object' && error !== null && 'status' in error && error.status === 409
    return { message, conflict }
  }

  function schedule_heartbeat(run_generation: number, delay_ms: number): void {
    if (run_generation !== generation || current_state.status !== 'held') return
    set_timer(() => {
      if (run_generation === generation && current_state.status === 'held') {
        request_acquire(current_state.token, 'heartbeat', run_generation)
      }
    }, delay_ms)
  }

  function accept_grant(request: LockRequest, grant: EditLockGrant): void {
    if (request.generation !== generation) {
      release_without_wait(grant.token)
      return
    }
    if (request.id !== latest_request_id) return
    if (!request.timed_out && active_request_id !== request.id) return
    clear_timer()
    if (active_request_id === request.id) active_request_id = null
    consecutive_failures = 0
    remembered_token = grant.token
    heartbeat_ms = grant.heartbeat_s * 1000
    publish({ status: 'held', token: grant.token, expires_at: grant.expires_at, message: null, conflict: false })
    schedule_heartbeat(request.generation, heartbeat_ms)
  }

  function fail_request(request: LockRequest, error: unknown): void {
    if (request.generation !== generation || request.id !== latest_request_id || request.timed_out || active_request_id !== request.id) return
    clear_timer()
    active_request_id = null
    const details = error_details(error)
    if (request.phase === 'initial') {
      if (details.conflict) remembered_token = null
      publish({ status: 'denied', token: null, expires_at: null, message: details.message, conflict: details.conflict })
      return
    }
    if (details.conflict) {
      remembered_token = null
      publish({ status: 'lost', token: null, expires_at: null, message: details.message, conflict: true })
      return
    }
    consecutive_failures += 1
    if (consecutive_failures >= max_failures) {
      publish({ status: 'lost', token: null, expires_at: null, message: details.message, conflict: false })
      return
    }
    schedule_heartbeat(request.generation, heartbeat_ms)
  }

  function request_timeout(request: LockRequest): void {
    if (request.generation !== generation || request.id !== latest_request_id || active_request_id !== request.id) return
    request.timed_out = true
    active_request_id = null
    if (request.phase === 'initial') {
      publish({ status: 'denied', token: null, expires_at: null, message: 'Server nicht erreichbar', conflict: false })
      return
    }
    consecutive_failures += 1
    if (consecutive_failures >= max_failures) {
      publish({ status: 'lost', token: null, expires_at: null, message: 'Server nicht erreichbar', conflict: false })
      return
    }
    schedule_heartbeat(request.generation, heartbeat_ms)
  }

  function request_acquire(token: string | null, phase: LockRequest['phase'], run_generation: number): void {
    if (run_generation !== generation) return
    const request: LockRequest = { generation: run_generation, id: ++request_id, phase, timed_out: false }
    latest_request_id = request.id
    active_request_id = request.id
    set_timer(() => request_timeout(request), request_timeout_ms)
    try {
      Promise.resolve(api.acquire(token)).then(
        grant => accept_grant(request, grant),
        error => fail_request(request, error),
      )
    } catch (error) {
      fail_request(request, error)
    }
  }

  function start(): void {
    if (current_state.status === 'acquiring' || current_state.status === 'held') return
    const run_generation = ++generation
    consecutive_failures = 0
    clear_timer()
    const token = current_state.status === 'lost' && !current_state.conflict ? remembered_token : null
    publish({ status: 'acquiring', token: null, expires_at: null, message: null, conflict: false })
    if (run_generation === generation) request_acquire(token, 'initial', run_generation)
  }

  function stop(): void {
    const token = current_state.status === 'held' ? current_state.token : remembered_token
    generation += 1
    clear_timer()
    active_request_id = null
    remembered_token = null
    consecutive_failures = 0
    publish(idle_state())
    if (token) release_without_wait(token)
  }

  return { start, stop, state: () => ({ ...current_state }) }
}

export const lock_held_prefix = 'Wird gerade bearbeitet von '

export function lock_holder_name(message: string | null): string | null {
  if (message === null || !message.startsWith(lock_held_prefix)) return null
  const name = message.slice(lock_held_prefix.length).trim()
  return name || null
}
