import { useCallback, useEffect, useState } from 'react'

export interface DataState<T> {
  data: T | undefined
  loading: boolean
  error: string | null
}

/** Load async data and reload when deps change. Stale responses are ignored. */
export function use_data<T>(loader: () => Promise<T>, deps: unknown[] = []): DataState<T> & { reload: () => void } {
  const [state, set_state] = useState<DataState<T>>({ data: undefined, loading: true, error: null })
  const [reload_count, set_reload_count] = useState(0)
  const reload = useCallback(() => set_reload_count(count => count + 1), [])

  useEffect(() => {
    let current = true
    set_state(previous => ({ ...previous, loading: true }))
    loader()
      .then(data => current && set_state({ data, loading: false, error: null }))
      .catch((error: unknown) => current && set_state({ data: undefined, loading: false, error: String(error) }))
    return () => { current = false }
  }, [...deps, reload_count])

  return { ...state, reload }
}
