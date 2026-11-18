import type { Connection, Edge } from '@xyflow/react'

export function last_incoming_edge(edges: readonly Edge[], target_id: string): Edge | undefined {
  for (let index = edges.length - 1; index >= 0; index -= 1) {
    if (edges[index].target === target_id) return edges[index]
  }
  return undefined
}

export function is_valid_connection(
  connection: Pick<Connection, 'source' | 'target'> & Pick<Edge, 'sourceHandle'>,
  locked_ids: ReadonlySet<string>,
  edges: readonly Edge[],
  excluded_edge_id?: string,
): boolean {
  const { source, sourceHandle, target } = connection
  if (!source || !target || source === target || locked_ids.has(source) || locked_ids.has(target)) return false
  return !edges.some(edge => edge.id !== excluded_edge_id
    && edge.source === source
    && (edge.sourceHandle ?? null) === (sourceHandle ?? null))
}

export function reconnectable_edge_ids(edges: readonly Edge[], locked_ids: ReadonlySet<string>): Set<string> {
  const latest_by_target = new Map<string, Edge>()
  for (const edge of edges) latest_by_target.set(edge.target, edge)
  return new Set(Array.from(latest_by_target.values())
    .filter(edge => !locked_ids.has(edge.source) && !locked_ids.has(edge.target))
    .map(edge => edge.id))
}
