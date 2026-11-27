import type { Connection, Edge } from '@xyflow/react'
import type { MacroDefinition, MacroBlock, MacroConnection, ModuleType, WorkflowModule } from '../../data/types'
// Node's direct TypeScript tests need the explicit extension.
// @ts-expect-error Bundler source resolution uses the same module.
import { assign_output, free_outputs, new_block_id, prune_definition } from '../../lib/macro_builder.ts'
import type { ModuleFlowNode } from '../workflow-editor/editor_types'

const empty_counters = { waiting: 0, running: 0, done: 0, failed: 0, discovered: 0 }

export function definition_to_flow(definition: MacroDefinition, module_types: ModuleType[]): { nodes: ModuleFlowNode[]; edges: Edge[] } {
  const types = new Map(module_types.map(module_type => [module_type.id, module_type]))
  const nodes = definition.blocks.flatMap((block): ModuleFlowNode[] => {
    const module_type = types.get(block.type_id)
    if (!module_type) return []
    const module: WorkflowModule = {
      id: block.id,
      type_id: block.type_id,
      name: block.name,
      description: '',
      position: { ...block.position },
      settings: { ...block.settings },
      counters: { ...empty_counters },
      locked: false,
    }
    return [{ id: block.id, type: 'module', position: { ...block.position }, data: { module, module_type } }]
  })
  const edges = definition.connections.map((connection, index): Edge => ({
    id: `builder-connection-${index}`,
    source: connection.source_block_id,
    sourceHandle: connection.source_output_id,
    target: connection.target_block_id,
    targetHandle: 'in',
    type: 'smoothstep',
  }))
  return { nodes, edges }
}

export function connection_from_edge(edge: Pick<Connection, 'source' | 'sourceHandle' | 'target'> | Pick<Edge, 'source' | 'sourceHandle' | 'target'>): MacroConnection {
  return {
    source_block_id: edge.source ?? '',
    source_output_id: edge.sourceHandle ?? '',
    target_block_id: edge.target ?? '',
  }
}

export function remove_connections_from_definition(definition: MacroDefinition, removed_connections: MacroConnection[]): MacroDefinition {
  const removed = new Set(removed_connections.map(connection => JSON.stringify([
    connection.source_block_id, connection.source_output_id, connection.target_block_id,
  ])))
  return {
    ...definition,
    connections: definition.connections.filter(connection => !removed.has(JSON.stringify([
      connection.source_block_id, connection.source_output_id, connection.target_block_id,
    ]))),
  }
}

export function add_block_to_definition(
  definition: MacroDefinition,
  module_type: ModuleType,
  module_types: ModuleType[],
  position: { x: number; y: number },
): MacroDefinition {
  const block: MacroBlock = {
    id: new_block_id(definition, module_type.id),
    type_id: module_type.id,
    name: module_type.name,
    position: { ...position },
    settings: Object.fromEntries(module_type.settings.flatMap(setting => (setting.default === undefined ? [] : [[setting.key, setting.default]]))),
  }
  let next: MacroDefinition = {
    ...definition,
    blocks: [...definition.blocks, block],
    entry_block_id: definition.entry_block_id === null && module_type.has_input ? block.id : definition.entry_block_id,
  }
  const matching_outputs = new Set(definition.outputs.map(output => output.id))
  for (const output of free_outputs(next, module_types)) {
    if (output.block_id !== block.id || !matching_outputs.has(output.output_id)) continue
    next = assign_output(next, output.block_id, output.output_id, output.output_id, module_types)
  }
  return prune_definition(next, module_types)
}
