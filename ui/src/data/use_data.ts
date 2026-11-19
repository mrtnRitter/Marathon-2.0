import { useCallback, useEffect, useRef, useState } from 'react'
import { create_reload_gate, subscribe_live, type LiveTable } from './live'

export interface DataState<T> {
  data: T | undefined
  loading: boolean
  error: string | null
}

/** Load async data and reload when deps change. Stale responses are ignored. */
export function use_data<T>(
  loader: () => Promise<T>,
  deps: unknown[] = [],
  options: { live?: readonly LiveTable[] } = {},
): DataState<T> & { reload: () => void } {
  const [state, set_state] = useState<DataState<T>>({ data: undefined, loading: true, error: null })
  const [reload_count, set_reload_count] = useState(0)
  const live_gate_ref = useRef<ReturnType<typeof create_reload_gate> | null>(null)
  const live_reload_ref = useRef(false)
  const live_gate_pending_ref = useRef(false)
  const previous_deps_ref = useRef<{ values: unknown[]; live_key: string | undefined } | null>(null)
  const live_key = options.live?.join(',')
  const reload = useCallback(() => {
    live_reload_ref.current = false
    set_reload_count(count => count + 1)
  }, [])

  useEffect(() => {
    if (!options.live) {
      live_gate_ref.current = null
      return
    }
    const gate = create_reload_gate(() => {
      live_reload_ref.current = true
      live_gate_pending_ref.current = true
      set_reload_count(count => count + 1)
    }, 250)
    live_gate_ref.current = gate
    const unsubscribe = subscribe_live(options.live, () => gate.request())
    return () => {
      unsubscribe()
      gate.dispose()
      if (live_gate_ref.current === gate) live_gate_ref.current = null
    }
  }, [live_key])

  useEffect(() => {
    const previous_deps = previous_deps_ref.current
    const deps_changed = previous_deps === null
      || previous_deps.live_key !== live_key
      || previous_deps.values.length !== deps.length
      || deps.some((dependency, index) => !Object.is(dependency, previous_deps.values[index]))
    previous_deps_ref.current = { values: [...deps], live_key }
    const live_gate_pending = live_gate_pending_ref.current
    live_gate_pending_ref.current = false
    const silent = live_reload_ref.current && !deps_changed
    live_reload_ref.current = false
    let current = true
    if (!silent) set_state(previous => ({ ...previous, loading: true }))
    const gate = live_gate_ref.current
    let request: Promise<T>
    try {
      request = loader()
    } catch (error) {
      try {
        if (!silent) throw error
      } finally {
        if (live_gate_pending) gate?.done()
      }
      return () => { current = false }
    }
    request
      .then(data => current && set_state({ data, loading: false, error: null }))
      .catch((error: unknown) => {
        if (current && !silent) set_state({ data: undefined, loading: false, error: String(error) })
      })
      .finally(() => {
        if (live_gate_pending) gate?.done()
      })
    return () => { current = false }
  }, [...deps, reload_count, live_key])

  return { ...state, reload }
}
