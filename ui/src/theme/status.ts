import type { AssetMessage, AssetStatus, JobStatus, LogLevel, StepStatus, Worker, WorkerStatus } from '../data/types'

export interface StatusMeta {
  label: string
  color: string
}

const color = (name: string) => `var(--status-${name})`

export const asset_status_meta: Record<AssetStatus, StatusMeta> = {
  pending: { label: 'Ausstehend', color: color('pending') },
  in_progress: { label: 'In Arbeit', color: color('in-progress') },
  done: { label: 'Erledigt', color: color('done') },
  failed: { label: 'Fehlgeschlagen', color: color('failed') },
  on_hold: { label: 'Angehalten', color: color('on-hold') },
}

export const asset_statuses = Object.keys(asset_status_meta) as AssetStatus[]

export const step_status_meta: Record<StepStatus, StatusMeta> = {
  waiting: { label: 'wartet', color: color('pending') },
  running: { label: 'läuft', color: color('in-progress') },
}

export const worker_status_meta: Record<WorkerStatus, StatusMeta> = {
  new: { label: 'neu', color: color('new') },
  ready: { label: 'bereit', color: color('done') },
  busy: { label: 'beschäftigt', color: color('in-progress') },
  offline: { label: 'offline', color: color('offline') },
  locked: { label: 'gesperrt', color: color('failed') },
  updating: { label: 'Update', color: color('updating') },
}

export const job_status_meta: Record<JobStatus, StatusMeta> = {
  open: { label: 'offen', color: color('pending') },
  assigned: { label: 'läuft', color: color('in-progress') },
  succeeded: { label: 'erfolgreich', color: color('done') },
  failed: { label: 'fehlgeschlagen', color: color('failed') },
  cancelled: { label: 'abgebrochen', color: color('offline') },
}

export const module_counter_meta: Record<'waiting' | 'running' | 'done' | 'failed', StatusMeta> = {
  waiting: { label: 'wartet', color: color('pending') },
  running: { label: 'läuft', color: color('in-progress') },
  done: { label: 'erledigt', color: color('done') },
  failed: { label: 'fehlgeschlagen', color: color('failed') },
}

export const log_level_color: Record<LogLevel, string> = {
  debug: 'var(--text-dim)',
  info: 'var(--text-muted)',
  warn: color('on-hold'),
  error: color('failed'),
}

export const active_meta = (active: boolean): StatusMeta =>
  active ? { label: 'aktiv', color: color('done') } : { label: 'pausiert', color: color('on-hold') }

export const is_online = (worker: Worker) => worker.status !== 'offline'

export const online_meta = (online: boolean): StatusMeta => online ? { label: 'Online', color: color('done') } : { label: 'Offline', color: color('offline') }

export const message_kind_color: Record<AssetMessage['kind'], string> = {
  success: color('done'),
  error: color('failed'),
  info: 'var(--text-muted)',
}

/** Color of a module output on the canvas; failure outputs use the failed status color. */
export const output_color = (output_id: string) => (output_id === 'failure' ? color('failed') : 'var(--text-muted)')
