import type { ModuleCounters } from '../../data/types'

const same_counters = (left: ModuleCounters, right: ModuleCounters): boolean =>
  left.waiting === right.waiting
  && left.running === right.running
  && left.done === right.done
  && left.failed === right.failed
  && left.discovered === right.discovered

export function merge_counters<N extends { id: string; data: { module: { counters: ModuleCounters } } }>(nodes: N[], modules: { id: string; counters: ModuleCounters }[]): N[] | null {
  const counters_by_id = new Map(modules.map(module => [module.id, module.counters]))
  let changed = false
  const merged = nodes.map(node => {
    const counters = counters_by_id.get(node.id)
    if (!counters || same_counters(node.data.module.counters, counters)) return node
    changed = true
    return { ...node, data: { ...node.data, module: { ...node.data.module, counters } } }
  })
  return changed ? merged : null
}
