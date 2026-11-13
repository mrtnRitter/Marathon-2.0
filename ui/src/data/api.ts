// Single data access point for all pages; talks to the server API.
import { notify } from '../components/Toast'
import type { Asset, AssetDetail, AssetStatus, DashboardData, GraphInput, JobStatusReport, ModuleActivity, ModuleType, ReprocessResult, Worker, Workflow, WorkflowGraph, WorkflowInput } from './types'

export class ApiError extends Error {
  readonly status: number
  readonly detail: string

  constructor(status: number, detail: string) {
    super(`Fehler ${status}: ${detail}`)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

const api_base = '/api/v1'

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
    notify('Server nicht erreichbar')
    throw new Error('Server nicht erreichbar')
  }

  if (response.status === 404 && allow_404) return null
  if (response.status === 204) return null
  if (!response.ok) {
    let detail = response.statusText
    try {
      const response_body = await response.json() as { detail?: unknown }
      if (typeof response_body.detail === 'string') detail = response_body.detail
    } catch {}
    if (!quiet) notify(`Fehler ${response.status}: ${detail}`)
    throw new ApiError(response.status, detail)
  }
  return await response.json() as T
}

export const get_workflows = (): Promise<Workflow[]> => request<Workflow[]>('/workflows').then(data => data!)

export const set_workflow_active = (id: string, active: boolean): Promise<Workflow> =>
  request<Workflow>(`/workflows/${encodeURIComponent(id)}/active`, { method: 'PUT', body: { active } }).then(data => data!)

export const get_workflow = (id: string): Promise<Workflow | null> =>
  request<Workflow>(`/workflows/${encodeURIComponent(id)}`, { allow_404: true })

export const get_workflow_graph = (id: string): Promise<WorkflowGraph | null> =>
  request<WorkflowGraph>(`/workflows/${encodeURIComponent(id)}/graph`, { allow_404: true })

export const get_module_types = (): Promise<ModuleType[]> => request<ModuleType[]>('/module-types').then(data => data!)

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

export const save_workflow_graph = (id: string, input: GraphInput): Promise<WorkflowGraph> =>
  request<WorkflowGraph>(`/workflows/${encodeURIComponent(id)}/graph`, { method: 'PUT', body: input }).then(data => data!)
