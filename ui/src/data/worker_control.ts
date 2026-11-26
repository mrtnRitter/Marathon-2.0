import type { AvailabilityConfig, Worker } from './types'

export interface WorkerControlView extends Worker {
  skill_names: string[]
}

export class WorkerControlError extends Error {
  readonly status: number
  readonly detail: string

  constructor(status: number, detail: string) {
    super(detail)
    this.name = 'WorkerControlError'
    this.status = status
    this.detail = detail
  }
}

async function worker_control_request<T>(path: string, options: { method?: 'GET' | 'POST' | 'PUT'; body?: unknown; signal?: AbortSignal } = {}): Promise<T> {
  const { method = 'GET', body, signal } = options
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  let response: Response
  try {
    response = await fetch(`/api/v1/worker-control${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: 'same-origin',
      signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(15_000)]) : AbortSignal.timeout(15_000),
    })
  } catch {
    throw new WorkerControlError(0, 'Server nicht erreichbar')
  }
  if (!response.ok) {
    let detail = response.statusText || 'Anfrage fehlgeschlagen'
    try {
      const result = await response.json() as { detail?: unknown }
      if (typeof result.detail === 'string') detail = result.detail
    } catch {}
    throw new WorkerControlError(response.status, detail)
  }
  return await response.json() as T
}

export const exchange_worker_control_token = (token: string, signal?: AbortSignal): Promise<WorkerControlView> =>
  worker_control_request<WorkerControlView>('/exchange', { method: 'POST', body: { token }, signal })

export const get_worker_control_self = (signal?: AbortSignal): Promise<WorkerControlView> =>
  worker_control_request<WorkerControlView>('/self', { signal })

export const update_worker_control_availability = (config: AvailabilityConfig, signal?: AbortSignal): Promise<WorkerControlView> =>
  worker_control_request<WorkerControlView>('/self/availability', { method: 'PUT', body: config, signal })

export const start_worker_control = (signal?: AbortSignal): Promise<WorkerControlView> =>
  worker_control_request<WorkerControlView>('/self/start', { method: 'POST', signal })

export const stop_drain_worker_control = (signal?: AbortSignal): Promise<WorkerControlView> =>
  worker_control_request<WorkerControlView>('/self/stop-drain', { method: 'POST', signal })

export const stop_now_worker_control = (signal?: AbortSignal): Promise<WorkerControlView> =>
  worker_control_request<WorkerControlView>('/self/stop-now', { method: 'POST', signal })
