export const live_tables = ['module_types', 'workflows', 'workflow_versions', 'workflow_modules', 'workflow_connections', 'workers', 'assets', 'jobs', 'job_assets', 'asset_history', 'log_lines'] as const
export type LiveTable = typeof live_tables[number]
export const live_url = '/api/v1/events'

export function parse_live_message(data: string): Set<string> | null {
  let message: unknown
  try {
    message = JSON.parse(data)
  } catch {
    return null
  }
  if (typeof message !== 'object' || message === null || !('tables' in message)) return null
  const tables = message.tables
  if (!Array.isArray(tables) || !tables.every(table => typeof table === 'string')) return null
  return new Set(tables)
}

export function affects(changed: ReadonlySet<string>, tables: readonly LiveTable[]): boolean {
  return changed.has('*') || tables.some(table => changed.has(table))
}

export interface LiveTimers {
  set_timeout: (fn: () => void, ms: number) => unknown
  clear_timeout: (handle: unknown) => void
}

export interface EventSourceLike {
  onopen: ((event: unknown) => void) | null
  onmessage: ((event: { data: string }) => void) | null
  onerror: ((event: unknown) => void) | null
  readonly readyState: number
  close(): void
}

export const offline_after_ms = 2000

const default_timers: LiveTimers = {
  set_timeout: (fn, ms) => globalThis.setTimeout(fn, ms),
  clear_timeout: handle => globalThis.clearTimeout(handle as number),
}

export function create_live_client(
  open: (url: string) => EventSourceLike,
  timers: LiveTimers = default_timers,
  reconnect_ms = 2000,
): {
  subscribe(tables: readonly LiveTable[], on_change: () => void): () => void
  subscribe_connection(on_change: (connected: boolean) => void): () => void
  is_connected(): boolean
} {
  type Subscriber = { tables: readonly LiveTable[]; on_change: () => void }
  const subscribers = new Set<Subscriber>()
  const connection_subscribers = new Set<{ on_change: (connected: boolean) => void }>()
  let source: EventSourceLike | undefined
  let source_open = false
  let reconnect_handle: unknown = undefined
  let offline_handle: unknown = undefined
  let had_error = false
  let connected = true

  const has_subscribers = () => subscribers.size > 0 || connection_subscribers.size > 0

  const notify = (subscriber: Subscriber) => {
    try {
      subscriber.on_change()
    } catch {
      // One subscriber must not prevent notifications to the others.
    }
  }

  const notify_connection = (subscriber: (connected: boolean) => void) => {
    try {
      subscriber(connected)
    } catch {
      // One subscriber must not prevent notifications to the others.
    }
  }

  const clear_offline_timer = () => {
    if (offline_handle === undefined) return
    timers.clear_timeout(offline_handle)
    offline_handle = undefined
  }

  const schedule_offline = () => {
    if (offline_handle !== undefined || source_open || !has_subscribers()) return
    offline_handle = timers.set_timeout(() => {
      offline_handle = undefined
      if (source_open || !has_subscribers() || !connected) return
      connected = false
      for (const subscriber of [...connection_subscribers]) notify_connection(subscriber.on_change)
    }, offline_after_ms)
  }

  const open_source = () => {
    if (source || !has_subscribers()) return
    const current_source = open(live_url)
    source = current_source
    source_open = false
    schedule_offline()
    current_source.onopen = () => {
      if (source !== current_source) return
      source_open = true
      clear_offline_timer()
      const was_connected = connected
      connected = true
      if (had_error) {
        had_error = false
        for (const subscriber of [...subscribers]) notify(subscriber)
      }
      if (!was_connected) {
        for (const subscriber of [...connection_subscribers]) notify_connection(subscriber.on_change)
      }
    }
    current_source.onmessage = event => {
      if (source !== current_source) return
      const changed = parse_live_message(event.data)
      if (!changed) return
      for (const subscriber of [...subscribers]) {
        if (affects(changed, subscriber.tables)) notify(subscriber)
      }
    }
    current_source.onerror = () => {
      if (source !== current_source) return
      had_error = true
      source_open = false
      schedule_offline()
      if (current_source.readyState !== 2) return
      source = undefined
      current_source.close()
      schedule_reconnect()
    }
  }

  const schedule_reconnect = () => {
    if (reconnect_handle !== undefined || !has_subscribers()) return
    reconnect_handle = timers.set_timeout(() => {
      reconnect_handle = undefined
      open_source()
    }, reconnect_ms)
  }

  const release_if_unused = () => {
    if (has_subscribers()) return
    if (reconnect_handle !== undefined) {
      timers.clear_timeout(reconnect_handle)
      reconnect_handle = undefined
    }
    clear_offline_timer()
    const current_source = source
    source = undefined
    source_open = false
    had_error = false
    current_source?.close()
  }

  return {
    subscribe(tables, on_change) {
      const subscriber = { tables: [...tables], on_change }
      subscribers.add(subscriber)
      open_source()
      let subscribed = true
      return () => {
        if (!subscribed) return
        subscribed = false
        subscribers.delete(subscriber)
        release_if_unused()
      }
    },
    subscribe_connection(on_change) {
      const subscriber = { on_change }
      connection_subscribers.add(subscriber)
      open_source()
      let subscribed = true
      return () => {
        if (!subscribed) return
        subscribed = false
        connection_subscribers.delete(subscriber)
        release_if_unused()
      }
    },
    is_connected() {
      return connected
    },
  }
}

const standard_client = create_live_client(url => new EventSource(url) as unknown as EventSourceLike)

export function subscribe_live(tables: readonly LiveTable[], on_change: () => void): () => void {
  return standard_client.subscribe(tables, on_change)
}

export function subscribe_connection(on_change: (connected: boolean) => void): () => void {
  return standard_client.subscribe_connection(on_change)
}

export function live_connected(): boolean {
  return standard_client.is_connected()
}

export function create_reload_gate(
  run: () => void,
  delay_ms: number,
  timers: LiveTimers = default_timers,
): { request(): void; done(): void; dispose(): void } {
  let timer_handle: unknown = undefined
  let in_flight = false
  let dirty = false
  let disposed = false

  const request = () => {
    if (disposed) return
    if (in_flight) {
      dirty = true
      return
    }
    if (timer_handle !== undefined) return
    timer_handle = timers.set_timeout(() => {
      timer_handle = undefined
      if (disposed) return
      in_flight = true
      run()
    }, delay_ms)
  }

  return {
    request,
    done() {
      if (disposed) return
      in_flight = false
      if (!dirty) return
      dirty = false
      request()
    },
    dispose() {
      if (disposed) return
      disposed = true
      dirty = false
      if (timer_handle !== undefined) {
        timers.clear_timeout(timer_handle)
        timer_handle = undefined
      }
    },
  }
}
