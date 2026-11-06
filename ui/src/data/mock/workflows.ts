import type { WorkflowConnection, WorkflowModule } from '../types'

type ModuleSeed = Omit<WorkflowModule, 'counters'> & { done: number }

export interface WorkflowSeed {
  id: string
  name: string
  description: string
  active: boolean
  version: number
  priority: number
  final_module: string
  modules: ModuleSeed[]
  connections: [string, string, string][]
}

const module = (id: string, type_id: string, name: string, x: number, y: number, done: number, settings: WorkflowModule['settings'] = {}): ModuleSeed =>
  ({ id, type_id, name, position: { x, y }, settings, done })

export const workflow_seeds: WorkflowSeed[] = [
  {
    id: 'wf-ingest',
    name: 'Ingest Kamera-Rushes',
    description: 'Neue Drehtage prüfen, Proxys erzeugen und für das Archiv bereitstellen',
    active: true,
    version: 3,
    priority: 10,
    final_module: 'm5',
    modules: [
      module('m1', 'input', 'Eingang', 290, 0, 412),
      module('m2', 'condition', 'Ist ARRI-Material?', 290, 160, 409, { field: 'camera', operator: 'enthält', value: 'ARRI' }),
      module('m3', 'transcode', 'ProRes Proxy', 60, 340, 351, { preset: 'ProRes 422 Proxy' }),
      module('m4', 'move_files', 'Quarantäne', 520, 340, 52, { target_path: '//nas01/quarantaene' }),
      module('m5', 'move_files', 'Archiv-Staging', 60, 530, 344, { target_path: '//nas01/archiv_staging' }),
      module('m6', 'wait', 'Fehlerpause', 360, 530, 6, { duration_s: 300 }),
    ],
    connections: [
      ['m1', 'success', 'm2'],
      ['m2', 'yes', 'm3'],
      ['m2', 'no', 'm4'],
      ['m3', 'success', 'm5'],
      ['m3', 'failure', 'm6'],
      ['m6', 'success', 'm3'],
    ],
  },
  {
    id: 'wf-proxy',
    name: 'Proxy-Erstellung',
    description: 'H.264-Sichtungskopien für Schnitt und Kunde',
    active: true,
    version: 7,
    priority: 5,
    final_module: 'm3',
    modules: [
      module('m1', 'input', 'Eingang', 200, 0, 1280),
      module('m2', 'transcode', 'H.264 Proxy', 200, 170, 1265, { preset: 'H.264 1080p', suffix: '_h264' }),
      module('m3', 'move_files', 'Proxy-Ablage', 40, 360, 1250, { target_path: '//nas02/proxies', mode: 'Kopieren' }),
      module('m4', 'wait', 'Retry-Pause', 360, 360, 14, { duration_s: 120 }),
    ],
    connections: [
      ['m1', 'success', 'm2'],
      ['m2', 'success', 'm3'],
      ['m2', 'failure', 'm4'],
      ['m4', 'success', 'm2'],
    ],
  },
  {
    id: 'wf-archive',
    name: 'Archiv LTO',
    description: 'Abgeschlossene Projekte sammeln und auf LTO schreiben',
    active: false,
    version: 2,
    priority: 1,
    final_module: 'm4',
    modules: [
      module('m1', 'input', 'Eingang', 220, 0, 96),
      module('m2', 'wait', 'Sammelpause', 220, 160, 96, { duration_s: 3600 }),
      module('m3', 'move_files', 'LTO-Staging', 220, 330, 90, { target_path: '//lto01/staging' }),
      module('m4', 'condition', 'Prüfsumme ok?', 220, 500, 88, { field: 'checksum_state', value: 'ok' }),
      module('m5', 'move_files', 'Nacharbeit', 400, 690, 3, { target_path: '//nas01/nacharbeit' }),
    ],
    connections: [
      ['m1', 'success', 'm2'],
      ['m2', 'success', 'm3'],
      ['m3', 'success', 'm4'],
      ['m4', 'no', 'm5'],
    ],
  },
]

export const to_connections = (seed: WorkflowSeed): WorkflowConnection[] =>
  seed.connections.map(([source, output, target]) => ({
    id: `${seed.id}-${source}-${output}`,
    source_module_id: source,
    source_output_id: output,
    target_module_id: target,
  }))

/** Module ids from the input to target_id along the connections (breadth first). */
export function path_to(seed: WorkflowSeed, target_id: string): string[] {
  const start = seed.modules.find(module => module.type_id === 'input')!.id
  const previous = new Map<string, string | null>([[start, null]])
  const queue = [start]
  while (queue.length) {
    const current = queue.shift()!
    if (current === target_id) break
    for (const [source, , target] of seed.connections) {
      if (source !== current || previous.has(target)) continue
      previous.set(target, current)
      queue.push(target)
    }
  }
  if (!previous.has(target_id)) return []
  const path: string[] = []
  for (let id: string | null = target_id; id; id = previous.get(id) ?? null) path.unshift(id)
  return path
}
