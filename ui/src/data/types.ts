// Data contract between UI and the server API (/api/v1). Field names match the Python side.

export type AssetStatus = 'pending' | 'in_progress' | 'done' | 'failed' | 'on_hold'
export type StepStatus = 'waiting' | 'running'
export type WorkerStatus = 'new' | 'ready' | 'busy' | 'offline' | 'locked' | 'updating'
export type JobStatus = 'open' | 'assigned' | 'succeeded' | 'failed' | 'cancelled'
export type LogLevel = 'debug' | 'info' | 'warn' | 'error'
export type FieldValue = string | number | boolean

export interface FieldSpec {
  key: string
  label: string
  type: 'text' | 'number' | 'select' | 'boolean'
  default?: FieldValue
  options?: string[]
  help?: string
}

export type ModuleSetting = FieldSpec

export interface ModuleOutput {
  id: string
  label: string
  description: string
}

export interface ModuleType {
  id: string
  name: string
  category: string
  icon: string
  description: string
  has_input: boolean
  outputs: ModuleOutput[]
  settings: ModuleSetting[]
}

export interface ModuleCounters {
  waiting: number
  running: number
  done: number
  failed: number
}

export interface WorkflowModule {
  id: string
  type_id: string
  name: string
  description: string
  position: { x: number; y: number }
  settings: Record<string, FieldValue>
  counters: ModuleCounters
  locked: boolean
}

export interface WorkflowConnection {
  id: string
  source_module_id: string
  source_output_id: string
  target_module_id: string
}

export interface WorkflowGraph {
  workflow_id: string
  version: number
  modules: WorkflowModule[]
  connections: WorkflowConnection[]
}

export interface Workflow {
  id: string
  name: string
  description: string
  active: boolean
  version: number
  priority: number
  asset_counts: Record<AssetStatus, number>
}

export interface Asset {
  id: string
  workflow_id: string
  name: string
  status: AssetStatus
  current_module: string | null
  current_module_id: string | null
  step_status: StepStatus | null
  updated_at: string
}

export interface TimelineEntry {
  module_id: string
  module_name: string
  job_id: string
  status: JobStatus
  attempts: number
  worker: string
  started_at: string
  duration_s: number
  output_label: string | null
}

export interface AssetMessage {
  ts: string
  kind: 'success' | 'error' | 'info'
  text: string
}

export interface LogLine {
  ts: string
  level: LogLevel
  text: string
}

export interface AssetDetail extends Asset {
  created_at: string
  metadata: Record<string, string>
  timeline: TimelineEntry[]
  messages: AssetMessage[]
  log: LogLine[]
}

export interface Job {
  id: string
  module_id: string
  status: JobStatus
  worker: string | null
  attempt: number
  progress: number
  asset_ids: string[]
}

export interface Worker {
  id: string
  name: string
  status: WorkerStatus
  capabilities: string[]
  max_parallel: number
  running_jobs: number
  client_version: string
  last_seen: string
  ip_address: string | null
}

export interface ModuleActivity {
  jobs: Job[]
  assets: Asset[]
  workers: Worker[]
  logs: LogLine[]
}

export interface DashboardData {
  workflows: Workflow[]
  totals: Record<AssetStatus, number>
  workers: Worker[]
}

export interface JobStatusCounts {
  counts: Record<JobStatus, number>
  total: number
}

export interface JobStatusReportRow extends JobStatusCounts {
  workflow_id: string
  workflow_name: string
}

export interface JobStatusReport {
  start: string
  end: string
  rows: JobStatusReportRow[]
  totals: JobStatusCounts
}
