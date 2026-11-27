import type { Connection, Edge } from '@xyflow/react'
import type { GraphInput, MacroSummary, ModuleType, WorkflowGraph, WorkflowModule } from '../../data/types'
import type { ModuleFlowNode } from './editor_types'

export function apply_lock(node: ModuleFlowNode): ModuleFlowNode {
  const locked = node.data.module.locked
  return { ...node, draggable: !locked, selectable: !locked, deletable: !locked, connectable: !locked }
}

export function view_nodes(nodes: ModuleFlowNode[], editing: boolean): ModuleFlowNode[] {
  return editing ? nodes : nodes.map(node => node.draggable === false ? node : { ...node, draggable: false })
}

export function lock_targets(nodes: ModuleFlowNode[], module_id: string): { ids: string[]; locked: boolean } {
  const module_node = nodes.find(node => node.id === module_id)
  if (!module_node) return { ids: [], locked: false }
  const locked = !module_node.data.module.locked
  const selected_nodes = nodes.filter(node => node.selected)
  const ids = !module_node.data.module.locked && module_node.selected && selected_nodes.length >= 2
    ? selected_nodes.map(node => node.id)
    : [module_id]
  return { ids, locked }
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

export function to_nodes(graph: WorkflowGraph, module_types: MacroSummary[]): ModuleFlowNode[] {
  return graph.modules.map(module => {
    const macro = module.blind ? undefined : module_types.find(type => type.id === module.type_id)
    const hull = module.hull ?? macro
    const resolved_module = {
      ...module,
      ...(hull ? { hull } : {}),
    }
    const module_type: ModuleType = hull ?? {
      id: module.type_id,
      name: module.name,
      category: '',
      icon: 'workflow',
      description: 'Modulhülle nicht verfügbar',
      has_input: true,
      outputs: [],
      settings: [],
    }
    return create_node(resolved_module, module_type)
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
    className: output === 'success' ? 'edge-success' : output === 'error' ? 'edge-error' : undefined,
  }
}

export function to_graph_input(base_version: number, nodes: ModuleFlowNode[], edges: Edge[]): GraphInput {
  return {
    base_version,
    modules: nodes.map(node => {
      const module = node.data.module
      return {
        id: module.id,
        type_id: module.type_id,
        name: module.name,
        description: module.description,
        position: node.position,
        settings: module.settings,
        locked: module.locked,
        macro_version: module.macro_version ?? null,
      }
    }),
    connections: edges.map(edge => ({
      source_module_id: edge.source,
      source_output_id: edge.sourceHandle ?? '',
      target_module_id: edge.target,
    })),
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

export function remove_edges_with_missing_outputs(module_id: string, outputs: ModuleType['outputs'], edges: Edge[]): Edge[] {
  const output_ids = new Set(outputs.map(output => output.id))
  return edges.filter(edge => edge.source !== module_id || output_ids.has(edge.sourceHandle ?? ''))
}

export function new_module(module_type: MacroSummary, position: { x: number; y: number }, index: number): WorkflowModule {
  return {
    id: `new-${Date.now()}-${index}`,
    type_id: module_type.id,
    name: module_type.name,
    description: '',
    position,
    settings: Object.fromEntries(module_type.settings.flatMap(spec => (spec.default === undefined ? [] : [[spec.key, spec.default]]))),
    counters: { waiting: 0, running: 0, done: 0, failed: 0, discovered: 0 },
    locked: false,
    access: module_type.access,
    blind: false,
    macro_version: module_type.version,
    hull: module_type,
  }
}
