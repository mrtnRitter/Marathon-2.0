import type { Asset, AssetDetail, AssetStatus, Job, LogLine, ModuleActivity, TimelineEntry, WorkflowGraph } from '../types'
import { create_random, minutes_ago } from './random'
import { module_types } from './module_types'
import { path_to, to_connections, workflow_seeds } from './workflows'
import { workers } from './workers'

const random = create_random(42)
const reels = ['A001', 'A002', 'B001', 'B003', 'C004']
const extensions = ['.mov', '.mxf', '.mov', '.ari', '.braw']
const status_pool: AssetStatus[] = ['pending', 'pending', 'pending', 'in_progress', 'in_progress', 'in_progress', 'done', 'done', 'failed', 'on_hold']
const busy_workers = workers.filter(worker => worker.status === 'busy' || worker.status === 'ready')

const find_seed = (workflow_id: string) => workflow_seeds.find(seed => seed.id === workflow_id)!
const workflow_modules = (workflow_id: string) => find_seed(workflow_id).modules
const work_modules = (workflow_id: string) => workflow_modules(workflow_id).filter(module => module.type_id !== 'input')

function create_asset(index: number): Asset {
  const seed = workflow_seeds[index % workflow_seeds.length]
  const status = seed.active ? random.pick(status_pool) : random.pick<AssetStatus>(['pending', 'on_hold', 'done'])
  const modules = work_modules(seed.id)
  const current = status === 'pending' ? null : status === 'done' ? modules.find(module => module.id === seed.final_module)! : random.pick(modules)
  const name = `${random.pick(reels)}_C${String(random.int(1, 99)).padStart(3, '0')}_${random.int(1000, 9999).toString(16).toUpperCase()}${random.pick(extensions)}`
  return {
    id: `a${String(index + 1).padStart(3, '0')}`,
    workflow_id: seed.id,
    name,
    status,
    current_module: current?.name ?? null,
    step_status: status === 'in_progress' ? random.pick(['waiting', 'running'] as const) : null,
    updated_at: minutes_ago(random.int(1, 60 * 30)),
  }
}

export const assets: Asset[] = Array.from({ length: 42 }, (_, index) => create_asset(index))

export function build_graph(workflow_id: string): WorkflowGraph {
  const seed = workflow_seeds.find(item => item.id === workflow_id)!
  const at_module = (name: string) => assets.filter(asset => asset.workflow_id === workflow_id && asset.current_module === name)
  return {
    workflow_id,
    version: seed.version,
    connections: to_connections(seed),
    modules: seed.modules.map(({ done, ...module }) => {
      const here = at_module(module.name)
      return {
        ...module,
        counters: {
          waiting: here.filter(asset => asset.step_status === 'waiting').length,
          running: here.filter(asset => asset.step_status === 'running').length,
          done,
          failed: here.filter(asset => asset.status === 'failed').length,
        },
      }
    }),
  }
}

const sample_log = (asset_name: string, failed: boolean): LogLine[] => [
  { ts: minutes_ago(42), level: 'info', text: `Job übernommen von RENDER-01 (Client 2.0.0)` },
  { ts: minutes_ago(42), level: 'debug', text: `Arbeitsordner: /tmp/marathon/job-7f3a` },
  { ts: minutes_ago(41), level: 'info', text: `Lese ${asset_name}` },
  { ts: minutes_ago(40), level: 'info', text: 'Fortschritt 25 %' },
  { ts: minutes_ago(38), level: 'info', text: 'Fortschritt 60 %' },
  ...(failed
    ? [
        { ts: minutes_ago(37), level: 'warn', text: 'Hardware-Encoder nicht verfügbar, Fallback auf Software' } as LogLine,
        { ts: minutes_ago(35), level: 'error', text: 'Zielordner nicht erreichbar: //nas01/archiv_staging (Zugriff verweigert)' } as LogLine,
      ]
    : [{ ts: minutes_ago(35), level: 'info', text: 'Fortschritt 100 %, Ausgang: Erfolg' } as LogLine]),
]

export function build_asset_detail(asset: Asset): AssetDetail {
  const seed = find_seed(asset.workflow_id)
  const current = seed.modules.find(module => module.name === asset.current_module)
  const passed = current ? path_to(seed, current.id).slice(1).map(id => seed.modules.find(module => module.id === id)!) : []
  const failed = asset.status === 'failed'
  const timeline: TimelineEntry[] = passed.map((module, index) => {
    const is_current = index === passed.length - 1 && asset.status !== 'done'
    const status = !is_current ? 'succeeded' : failed ? 'failed' : asset.step_status === 'running' ? 'assigned' : asset.status === 'on_hold' ? 'cancelled' : 'open'
    return {
      module_id: module.id,
      module_name: module.name,
      job_id: `job-${asset.id}-${module.id}`,
      status,
      attempts: is_current && failed ? 3 : 1,
      worker: status === 'open' ? '–' : busy_workers[index % busy_workers.length].name,
      started_at: minutes_ago(60 - index * 9),
      duration_s: status === 'open' ? 0 : 40 + index * 37,
      output_label: status === 'succeeded' ? 'Erfolg' : status === 'failed' ? 'Fehler' : null,
    }
  })
  return {
    ...asset,
    created_at: minutes_ago(60 * 26),
    metadata: {
      camera: asset.name.startsWith('A') ? 'ARRI Alexa 35' : 'Blackmagic URSA 12K',
      resolution: '4608 x 3164',
      fps: '25',
      codec: asset.name.endsWith('.mov') ? 'ProRes 4444 XQ' : 'ARRIRAW',
      project: 'TATORT_2026_EP04',
      shooting_day: 'DT 12',
      checksum_xxh64: 'a9f3c21e77d04b18',
    },
    timeline,
    messages: [
      { ts: asset.updated_at, kind: failed ? 'error' : 'success', text: failed ? `Fehler in „${asset.current_module}“: Zielordner nicht erreichbar` : 'Letzter Schritt erfolgreich' },
      { ts: minutes_ago(60 * 26), kind: 'info', text: 'Asset per Import zur Liste hinzugefügt' },
    ],
    log: asset.status === 'pending' ? [] : sample_log(asset.name, failed),
  }
}

export function build_module_activity(workflow_id: string, module_id: string): ModuleActivity {
  const module = workflow_modules(workflow_id).find(item => item.id === module_id)
  const module_assets = assets.filter(asset => asset.workflow_id === workflow_id && asset.current_module === module?.name)
  const jobs: Job[] = module_assets.map((asset, index) => ({
    id: `job-${asset.id}-${module_id}`,
    module_id,
    status: asset.status === 'failed' ? 'failed' : asset.step_status === 'running' ? 'assigned' : asset.status === 'done' ? 'succeeded' : 'open',
    worker: asset.step_status === 'running' || asset.status === 'failed' ? busy_workers[index % busy_workers.length].name : null,
    attempt: asset.status === 'failed' ? 3 : 1,
    progress: asset.step_status === 'running' ? 15 + ((index * 29) % 80) : asset.status === 'done' ? 100 : 0,
    asset_ids: [asset.id],
  }))
  const worker_names = new Set(jobs.map(job => job.worker))
  const module_type = module_types.find(type => type.id === module?.type_id)
  return {
    jobs,
    assets: module_assets,
    workers: workers.filter(worker => worker_names.has(worker.name)),
    logs: [
      { ts: minutes_ago(30), level: 'info', text: `${module?.name ?? module_id}: ${jobs.length} Jobs in der Liste` },
      { ts: minutes_ago(22), level: 'debug', text: `Modultyp ${module_type?.name ?? '?'} (${module_type?.id ?? '?'})` },
      { ts: minutes_ago(12), level: 'info', text: 'Job an RENDER-01 zugeteilt' },
      ...(jobs.some(job => job.status === 'failed') ? [{ ts: minutes_ago(8), level: 'error', text: 'Job fehlgeschlagen nach 3 Versuchen' } as LogLine] : []),
      { ts: minutes_ago(1), level: 'info', text: 'Lebenszeichen von RENDER-01: 3/4 Jobs aktiv' },
    ],
  }
}
