// Single data access point for all pages; talks to the server API.
import { notify } from '../components/Toast'
import type { Asset, AssetDetail, AssetStatus, DashboardData, ModuleActivity, ModuleType, Worker, Workflow, WorkflowGraph } from './types'

const api_base = '/api/v1'

async function request<T>(path: string, allow_404 = false): Promise<T | null> {
  let response: Response
  try {
    response = await fetch(`${api_base}${path}`, { headers: { Accept: 'application/json' }, signal: AbortSignal.timeout(15_000) })
  } catch {
    notify('Server nicht erreichbar')
    throw new Error('Server nicht erreichbar')
  }

  if (response.status === 404 && allow_404) return null
  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = await response.json() as { detail?: unknown }
      if (typeof body.detail === 'string') detail = body.detail
    } catch {}
    notify(`Fehler ${response.status}: ${detail}`)
    throw new Error(`Fehler ${response.status}: ${detail}`)
  }
  return await response.json() as T
}

export const get_workflows = (): Promise<Workflow[]> => request<Workflow[]>('/workflows').then(data => data!)

export const get_workflow = (id: string): Promise<Workflow | null> =>
  request<Workflow>(`/workflows/${encodeURIComponent(id)}`, true)

export const get_workflow_graph = (id: string): Promise<WorkflowGraph | null> =>
  request<WorkflowGraph>(`/workflows/${encodeURIComponent(id)}/graph`, true)

export const get_module_types = (): Promise<ModuleType[]> => request<ModuleType[]>('/module-types').then(data => data!)

export const get_module_activity = (workflow_id: string, module_id: string): Promise<ModuleActivity> =>
  request<ModuleActivity>(`/workflows/${encodeURIComponent(workflow_id)}/modules/${encodeURIComponent(module_id)}/activity`).then(data => data!)

export function get_assets(filter: { workflow_id?: string; status?: AssetStatus } = {}): Promise<Asset[]> {
  const params = new URLSearchParams()
  if (filter.workflow_id) params.set('workflow_id', filter.workflow_id)
  if (filter.status) params.set('status', filter.status)
  const query = params.toString()
  return request<Asset[]>(`/assets${query ? `?${query}` : ''}`).then(data => data!)
}

export function get_asset_detail(id: string): Promise<AssetDetail | null> {
  return request<AssetDetail>(`/assets/${encodeURIComponent(id)}`, true)
}

export const get_workers = (): Promise<Worker[]> => request<Worker[]>('/workers').then(data => data!)

export const get_dashboard = (): Promise<DashboardData> => request<DashboardData>('/dashboard').then(data => data!)
