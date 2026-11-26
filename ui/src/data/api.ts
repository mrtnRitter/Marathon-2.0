// Single data access point for all pages; talks to the server API.
import { notify } from '../components/Toast'
import type { Asset, AssetDetail, AssetStatus, AuthUser, AvailabilityConfig, ClientInfo, DashboardData, DirListing, GraphImpact, GraphInput, JobStatusReport, ModuleActivity, ModuleType, ReprocessResult, Settings, SmtpSettings, SmtpSettingsInput, UserDirectoryEntry, UserRecord, UserSession, UserRole, Worker, WorkerInput, Workflow, WorkflowGraph, WorkflowInput, WorkflowShare, WorkflowShareAccess } from './types'

export class ApiError extends Error {
  readonly status: number
  readonly detail: string

  constructor(status: number, detail: string) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

const api_base = '/api/v1'
let login_redirect_started = false

async function request<T>(path: string, options: { method?: 'GET' | 'POST' | 'PUT' | 'DELETE'; body?: unknown; allow_404?: boolean; quiet?: boolean } = {}): Promise<T | null> {
  const { method = 'GET', body, allow_404 = false, quiet = false } = options
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (body !== undefined) headers['Content-Type'] = 'application/json'

  let response: Response
  try {
    response = await fetch(`${api_base}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(15_000),
    })
  } catch {
    if (!quiet) notify('Server nicht erreichbar')
    throw new Error('Server nicht erreichbar')
  }

  if (response.status === 404 && allow_404) return null
  const should_redirect_to_login = response.status === 401
    && !['/auth/admin-code', '/auth/request-code', '/auth/confirm'].includes(path.split('?')[0])
    && window.location.pathname !== '/login'
  if (should_redirect_to_login && !login_redirect_started) {
    login_redirect_started = true
    const next = `${window.location.pathname}${window.location.search}`
    window.location.assign(`/login?next=${encodeURIComponent(next)}`)
  }
  if (response.status === 204) return null
  if (!response.ok) {
    let detail = response.statusText
    try {
      const response_body = await response.json() as { detail?: unknown }
      if (typeof response_body.detail === 'string') detail = response_body.detail
    } catch {}
    if (!quiet && !should_redirect_to_login) notify(detail)
    throw new ApiError(response.status, detail)
  }
  return await response.json() as T
}

export const request_auth_code = (email: string): Promise<{ detail: string }> =>
  request<{ detail: string }>('/auth/request-code', { method: 'POST', body: { email }, quiet: true }).then(data => data!)

export const confirm_auth_code = (email: string, code: string): Promise<AuthUser> =>
  request<AuthUser>('/auth/confirm', { method: 'POST', body: { email, code }, quiet: true }).then(data => data!)

export const confirm_admin_code = (code: string): Promise<AuthUser> =>
  request<AuthUser>('/auth/admin-code', { method: 'POST', body: { code }, quiet: true }).then(data => data!)

export const get_auth_me = (): Promise<AuthUser> =>
  request<AuthUser>('/auth/me', { quiet: true }).then(data => data!)

export async function logout(): Promise<void> {
  await request<null>('/auth/logout', { method: 'POST' })
}

export const get_users = (): Promise<UserRecord[]> => request<UserRecord[]>('/users').then(data => data!)

export const get_user_directory = (): Promise<UserDirectoryEntry[]> =>
  request<UserDirectoryEntry[]>('/users/directory').then(data => data!)

export const update_user_role = async (id: string, role: UserRole): Promise<void> => {
  await request<unknown>(`/users/${encodeURIComponent(id)}`, { method: 'PUT', body: { role } })
}

export async function delete_user(id: string): Promise<void> {
  await request<null>(`/users/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

export const get_user_sessions = (id: string): Promise<UserSession[]> =>
  request<UserSession[]>(`/users/${encodeURIComponent(id)}/sessions`).then(data => data!)

export async function delete_user_session(id: string, session_id: string): Promise<void> {
  await request<null>(`/users/${encodeURIComponent(id)}/sessions/${encodeURIComponent(session_id)}`, { method: 'DELETE' })
}

export async function delete_user_sessions(id: string): Promise<void> {
  await request<null>(`/users/${encodeURIComponent(id)}/sessions`, { method: 'DELETE' })
}

export async function unlock_user(id: string): Promise<void> {
  await request<null>(`/users/${encodeURIComponent(id)}/lock`, { method: 'DELETE' })
}

export async function restart_server(): Promise<void> {
  await request<{ detail: string }>('/server/restart', { method: 'POST' })
}

export const get_workflows = (): Promise<Workflow[]> => request<Workflow[]>('/workflows').then(data => data!)

export const set_workflow_active = (id: string, active: boolean): Promise<Workflow> =>
  request<Workflow>(`/workflows/${encodeURIComponent(id)}/active`, { method: 'PUT', body: { active } }).then(data => data!)

export const get_workflow = (id: string): Promise<Workflow | null> =>
  request<Workflow>(`/workflows/${encodeURIComponent(id)}`, { allow_404: true })

export const get_workflow_shares = (id: string): Promise<WorkflowShare[]> =>
  request<{ shares: WorkflowShare[] }>(`/workflows/${encodeURIComponent(id)}/shares`).then(data => data!.shares)

export const set_workflow_shares = (id: string, shares: { user_id: string; access: WorkflowShareAccess }[]): Promise<WorkflowShare[]> =>
  request<{ shares: WorkflowShare[] }>(`/workflows/${encodeURIComponent(id)}/shares`, { method: 'PUT', body: { shares } }).then(data => data!.shares)

export const set_workflow_owner = (id: string, user_id: string): Promise<Workflow> =>
  request<Workflow>(`/workflows/${encodeURIComponent(id)}/owner`, { method: 'PUT', body: { user_id } }).then(data => data!)

export const get_workflow_graph = (id: string, options: { quiet?: boolean } = {}): Promise<WorkflowGraph | null> => request<WorkflowGraph>(`/workflows/${encodeURIComponent(id)}/graph`, { allow_404: true, quiet: options.quiet })

export const get_module_types = (): Promise<ModuleType[]> => request<ModuleType[]>('/module-types').then(data => data!)

export const list_dirs = (path?: string | null): Promise<DirListing> =>
  request<DirListing>(`/fs/dirs${path === undefined || path === null ? '' : `?path=${encodeURIComponent(path)}`}`, { quiet: true }).then(data => data!)

export const get_module_activity = (workflow_id: string, module_id: string): Promise<ModuleActivity> =>
  request<ModuleActivity>(`/workflows/${encodeURIComponent(workflow_id)}/modules/${encodeURIComponent(module_id)}/activity`).then(data => data!)

export const reprocess_assets = (asset_ids: string[], target_workflow_id: string | null): Promise<ReprocessResult> =>
  request<ReprocessResult>('/assets/reprocess', { method: 'POST', body: { asset_ids, target_workflow_id } }).then(data => data!)

export function get_assets(filter: { workflow_id?: string; status?: AssetStatus } = {}): Promise<Asset[]> {
  const params = new URLSearchParams()
  if (filter.workflow_id) params.set('workflow_id', filter.workflow_id)
  if (filter.status) params.set('status', filter.status)
  const query = params.toString()
  return request<Asset[]>(`/assets${query ? `?${query}` : ''}`).then(data => data!)
}

export function get_asset_detail(id: string): Promise<AssetDetail | null> {
  return request<AssetDetail>(`/assets/${encodeURIComponent(id)}`, { allow_404: true })
}

export const get_workers = (): Promise<Worker[]> => request<Worker[]>('/workers').then(data => data!)

export const update_worker = (id: string, input: WorkerInput): Promise<Worker> =>
  request<Worker>(`/workers/${encodeURIComponent(id)}`, { method: 'PUT', body: input }).then(data => data!)

export const start_worker = (id: string): Promise<Worker> =>
  request<Worker>(`/workers/${encodeURIComponent(id)}/start`, { method: 'POST' }).then(data => data!)

export const stop_drain_worker = (id: string): Promise<Worker> =>
  request<Worker>(`/workers/${encodeURIComponent(id)}/stop-drain`, { method: 'POST' }).then(data => data!)

export const stop_now_worker = (id: string): Promise<Worker> =>
  request<Worker>(`/workers/${encodeURIComponent(id)}/stop-now`, { method: 'POST' }).then(data => data!)

export const approve_worker = (id: string): Promise<Worker> =>
  request<Worker>(`/workers/${encodeURIComponent(id)}/approve`, { method: 'POST' }).then(data => data!)

export async function delete_worker(id: string): Promise<void> {
  await request<{ ok: true }>(`/workers/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

export const update_worker_availability = (id: string, config: AvailabilityConfig): Promise<Worker> =>
  request<Worker>(`/workers/${encodeURIComponent(id)}/availability`, { method: 'PUT', body: config }).then(data => data!)

export const get_client_info = (): Promise<ClientInfo> => request<ClientInfo>('/client').then(data => data!)

export const client_download_url = `${api_base}/client/download`

export const get_settings = (): Promise<Settings> => request<Settings>('/settings').then(data => data!)

export const save_settings = (input: Settings): Promise<Settings> =>
  request<Settings>('/settings', { method: 'PUT', body: input }).then(data => data!)

export const get_smtp_settings = (): Promise<SmtpSettings> =>
  request<SmtpSettings>('/settings/smtp').then(data => data!)

export const save_smtp_settings = (input: SmtpSettingsInput): Promise<SmtpSettings> =>
  request<SmtpSettings>('/settings/smtp', { method: 'PUT', body: input }).then(data => data!)

export const send_smtp_test = (address: string): Promise<{ detail: string }> =>
  request<{ detail: string }>('/settings/smtp/test', { method: 'POST', body: { address } }).then(data => data!)

export const get_dashboard = (): Promise<DashboardData> => request<DashboardData>('/dashboard').then(data => data!)

export function get_job_status_report(filter: { start: string; end: string; workflow_id?: string }): Promise<JobStatusReport> {
  const params = new URLSearchParams({ start: filter.start, end: filter.end })
  if (filter.workflow_id) params.set('workflow_id', filter.workflow_id)
  return request<JobStatusReport>(`/reports/job-status?${params}`).then(data => data!)
}

export function job_status_csv_url(filter: { start: string; end: string; workflow_id?: string }): string {
  const params = new URLSearchParams({ start: filter.start, end: filter.end })
  if (filter.workflow_id) params.set('workflow_id', filter.workflow_id)
  return `/api/v1/reports/job-status.csv?${params}`
}

export const workflow_name_taken_detail = 'Ein Workflow mit diesem Namen existiert bereits'

export const create_workflow = (input: WorkflowInput, options: { quiet?: boolean } = {}): Promise<Workflow> =>
  request<Workflow>('/workflows', { method: 'POST', body: input, quiet: options.quiet }).then(data => data!)

export const update_workflow = (id: string, input: WorkflowInput, options: { quiet?: boolean } = {}): Promise<Workflow> =>
  request<Workflow>(`/workflows/${encodeURIComponent(id)}`, { method: 'PUT', body: input, quiet: options.quiet }).then(data => data!)

export const duplicate_workflow = (id: string): Promise<Workflow> =>
  request<Workflow>(`/workflows/${encodeURIComponent(id)}/duplicate`, { method: 'POST' }).then(data => data!)

export const delete_workflow = async (id: string): Promise<void> => {
  await request<null>(`/workflows/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

export const get_graph_impact = (id: string, input: GraphInput): Promise<GraphImpact> =>
  request<GraphImpact>(`/workflows/${encodeURIComponent(id)}/graph/impact`, { method: 'POST', body: input }).then(data => data!)

export const save_workflow_graph = (id: string, input: GraphInput): Promise<WorkflowGraph> =>
  request<WorkflowGraph>(`/workflows/${encodeURIComponent(id)}/graph`, { method: 'PUT', body: input }).then(data => data!)
