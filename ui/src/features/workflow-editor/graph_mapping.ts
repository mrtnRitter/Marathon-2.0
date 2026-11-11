import type { Connection, Edge } from '@xyflow/react'
import type { ModuleType, WorkflowGraph, WorkflowModule } from '../../data/types'
import type { ModuleFlowNode } from './editor_types'

export function apply_lock(node: ModuleFlowNode): ModuleFlowNode {
  const locked = node.data.module.locked
  return { ...node, draggable: !locked, selectable: !locked, deletable: !locked, connectable: !locked }
}

export function locked_ids(nodes: ModuleFlowNode[]): Set<string> {
  return new Set(nodes.filter(node => node.data.module.locked).map(node => node.id))
}

export function lock_edges(edges: Edge[], locked_module_ids: Set<string>): Edge[] {
  return edges.map(edge => {
    const locked = locked_module_ids.has(edge.source) || locked_module_ids.has(edge.target)
    return { ...edge, deletable: !locked, selectable: !locked }
  })
}

export function to_nodes(graph: WorkflowGraph, module_types: ModuleType[]): ModuleFlowNode[] {
  return graph.modules.flatMap(module => {
    const module_type = module_types.find(type => type.id === module.type_id)
    return module_type ? [create_node(module, module_type)] : []
  })
}

export const create_node = (module: WorkflowModule, module_type: ModuleType): ModuleFlowNode =>
  apply_lock({ id: module.id, type: 'module', position: module.position, data: { module, module_type } })

export function create_edge(connection: Connection | Edge, running = false): Edge {
  const output = connection.sourceHandle ?? ''
  return {
    id: `${connection.source}-${output}-${connection.target}`,
    source: connection.source,
    sourceHandle: output,
    target: connection.target,
    targetHandle: 'in',
    type: 'smoothstep',
    animated: running,
    className: output === 'failure' ? 'edge-failure' : undefined,
  }
}

export function to_edges(graph: WorkflowGraph): Edge[] {
  const running = new Set(graph.modules.filter(module => module.counters.running > 0).map(module => module.id))
  return graph.connections.map(connection => create_edge({
    source: connection.source_module_id,
    sourceHandle: connection.source_output_id,
    target: connection.target_module_id,
    targetHandle: 'in',
  }, running.has(connection.target_module_id)))
}

export function new_module(module_type: ModuleType, position: { x: number; y: number }, index: number): WorkflowModule {
  return {
    id: `new-${Date.now()}-${index}`,
    type_id: module_type.id,
    name: module_type.name,
    description: '',
    position,
    settings: Object.fromEntries(module_type.settings.flatMap(spec => (spec.default === undefined ? [] : [[spec.key, spec.default]]))),
    counters: { waiting: 0, running: 0, done: 0, failed: 0 },
    locked: false,
  }
}
