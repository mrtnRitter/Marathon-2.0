import type { Worker } from '../data/types'

export const can_start = (worker: Worker): boolean =>
  worker.approved && (worker.status === 'stopped' || worker.status === 'draining')

export const can_stop_drain = (worker: Worker): boolean => worker.status === 'ready'

export const can_stop_now = (worker: Worker): boolean =>
  worker.status === 'ready' || worker.status === 'draining'

export const can_delete = (worker: Worker): boolean =>
  worker.id !== 'server-worker' && worker.running_jobs === 0

export const availability_text = (worker: Worker): string =>
  worker.available ? 'ja' : worker.availability_reason || 'nein'
