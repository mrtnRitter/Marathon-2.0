// Data contract between UI and the server API (/api/v1). Field names match the Python side.

export type AssetStatus = 'pending' | 'in_progress' | 'done' | 'failed' | 'on_hold'
export type StepStatus = 'waiting' | 'running'
export type WorkerStatus = 'ready' | 'draining' | 'stopping' | 'stopped'
export type AvailabilityDay = 'mon' | 'tue' | 'wed' | 'thu' | 'fri' | 'sat' | 'sun'

export interface AvailabilityConfig {
  schedule: { enabled: boolean; days: Record<AvailabilityDay, { start: string; end: string }[]> }
  idle: { enabled: boolean; minutes: number; max_cpu_percent: number; max_ram_percent: number; max_gpu_percent: number }
  blocked_processes: string[]
}

export interface ClientInfo {
  available: boolean
  version: string | null
  file_name: string
  detail: string | null
}
export type JobStatus = 'open' | 'assigned' | 'running' | 'cancelling' | 'succeeded' | 'failed' | 'cancelled' | 'interrupted'
export type LogLevel = 'debug' | 'info' | 'warn' | 'error'
export interface PrioEntry { name: string; metadata: Record<string, unknown> }

export type UserRole = 'user' | 'admin'

export type WorkflowAccess = 'admin' | 'owner' | 'edit' | 'run' | 'view'
export type WorkflowShareAccess = 'view' | 'run' | 'edit'

export interface WorkflowOwner {
  id: string
  email: string
  display_name: string
}

export interface UserDirectoryEntry {
  id: string
  email: string
  display_name: string
}

export interface WorkflowShare {
  user_id: string
  email: string
  display_name: string
  access: WorkflowShareAccess
}

export interface AuthUser {
  id: string
  email: string
  display_name: string
  role: UserRole
}

export interface UserRecord extends AuthUser {
  protected: boolean
  created_at: string
  last_login_at: string | null
  session_count: number
  locked_until: string | null
}

export interface UserSession {
  id: string
  user_agent: string | null
  created_at: string
  last_seen_at: string
  expires_at: string
  current: boolean
}

export type FieldValue = string | number | boolean | PrioEntry[]

export interface DirEntry {
  name: string
  path: string
}

export interface DirListing {
  path: string | null
  parent: string | null
  dirs: DirEntry[]
  truncated: boolean
}

export interface FieldSpec {
  key: string
  label: string
  type: 'text' | 'number' | 'select' | 'boolean' | 'path' | 'extensions' | 'priolist'
  default?: FieldValue
  options?: string[]
  help?: string
  min?: number
  max?: number
  unit?: string
  exclusive_with?: string
  list_label?: string
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
  discovered: number
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

export type ScheduleUnit = 'minutes' | 'hours' | 'days' | 'weeks' | 'months'

export interface ScheduleInput {
  every: number
  unit: ScheduleUnit
  start: string
}

export interface WorkflowSchedule extends ScheduleInput {
  next_run_at: string
}

export interface Workflow {
  id: string
  owner: WorkflowOwner | null
  access: WorkflowAccess
  name: string
  description: string
  active: boolean
  pausing: boolean
  issues: string[]
  version: number
  schedule?: WorkflowSchedule | null
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
  asset?: string | null
  worker?: string | null
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
  approved: boolean
  available: boolean
  availability_reason: string
  availability_config: AvailabilityConfig
  availability_revision: number
  online: boolean
  capabilities: string[]
  skills: string[]
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

export interface GraphImpact {
  removed_modules: { id: string; name: string; assets: number; jobs: number }[]
  assets: number
  jobs: number
  outcomes: { rerouted: number; done: number; failed: number }
}

export interface WorkerInput {
  name: string
  skills: string[]
}

export interface Settings {
  folder_dialog: { server_address: string }
  proxy: { public_url: string; builtin: boolean; trusted_proxies: string[] }
  auth: { allowed_domains: string[] }
}

export type SmtpEncryption = 'none' | 'starttls' | 'tls'

export interface SmtpSettings {
  host: string
  port: number
  username: string
  encryption: SmtpEncryption
  from_address: string
  from_name: string
  password_set: boolean
}

export interface SmtpSettingsInput extends Omit<SmtpSettings, 'password_set'> {
  password: string | null
}

export interface WorkflowInput {
  name: string
  description: string
  schedule: ScheduleInput | null
}

export interface ReprocessResult {
  asset_ids: string[]
}

export type ModuleInput = Omit<WorkflowModule, 'counters'>
export type ConnectionInput = Omit<WorkflowConnection, 'id'>

export interface GraphInput {
  base_version: number
  modules: ModuleInput[]
  connections: ConnectionInput[]
}
