// Single data access point for all pages. Today it serves sample data; later only this file switches to fetch().
import { asset_statuses } from '../theme/status'
import { assets, build_asset_detail, build_graph, build_module_activity } from './mock/assets'
import { module_types } from './mock/module_types'
import { workflow_seeds } from './mock/workflows'
import { workers } from './mock/workers'
import type { Asset, AssetDetail, AssetStatus, DashboardData, ModuleActivity, ModuleType, Worker, Workflow, WorkflowGraph } from './types'

const latency_ms = 120

const respond = <T,>(data: T): Promise<T> => new Promise(resolve => setTimeout(() => resolve(structuredClone(data)), latency_ms))

function count_statuses(items: Asset[]): Record<AssetStatus, number> {
  return Object.fromEntries(asset_statuses.map(status => [status, items.filter(item => item.status === status).length])) as Record<AssetStatus, number>
}

function build_workflows(): Workflow[] {
  return workflow_seeds.map(({ id, name, description, active, version, priority }) => ({
    id, name, description, active, version, priority,
    asset_counts: count_statuses(assets.filter(asset => asset.workflow_id === id)),
  }))
}

export const get_workflows = (): Promise<Workflow[]> => respond(build_workflows())

export const get_workflow = (id: string): Promise<Workflow | null> => respond(build_workflows().find(workflow => workflow.id === id) ?? null)

export const get_workflow_graph = (id: string): Promise<WorkflowGraph | null> =>
  respond(workflow_seeds.some(seed => seed.id === id) ? build_graph(id) : null)

export const get_module_types = (): Promise<ModuleType[]> => respond(module_types)

export const get_module_activity = (workflow_id: string, module_id: string): Promise<ModuleActivity> =>
  respond(build_module_activity(workflow_id, module_id))

export function get_assets(filter: { workflow_id?: string; status?: AssetStatus } = {}): Promise<Asset[]> {
  return respond(assets.filter(asset =>
    (!filter.workflow_id || asset.workflow_id === filter.workflow_id) && (!filter.status || asset.status === filter.status)))
}

export function get_asset_detail(id: string): Promise<AssetDetail | null> {
  const asset = assets.find(item => item.id === id)
  return respond(asset ? build_asset_detail(asset) : null)
}

export const get_workers = (): Promise<Worker[]> => respond(workers)

export const get_dashboard = (): Promise<DashboardData> => respond({
  workflows: build_workflows(),
  totals: count_statuses(assets),
  workers_online: workers.filter(worker => worker.status !== 'offline').length,
  workers_total: workers.length,
})
