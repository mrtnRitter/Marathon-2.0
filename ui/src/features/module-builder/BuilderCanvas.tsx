import { useMemo, useState, type DragEvent } from 'react'
import {
  Background, BackgroundVariant, Controls, ReactFlow, ReactFlowProvider, useReactFlow,
  type Connection, type Edge, type EdgeChange, type NodeChange,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { Icon } from '../../components/Icon'
import type { MacroDefinition, ModuleType } from '../../data/types'
import { is_valid_connection } from '../workflow-editor/connections'
import { EditorActionsContext, EditorModeContext, drag_mime, type ModuleFlowNode } from '../workflow-editor/editor_types'
import { ModuleNode } from '../workflow-editor/ModuleNode'
import { ModulePalette } from '../workflow-editor/ModulePalette'
import { connection_from_edge, definition_to_flow } from './builder_mapping'
import '../workflow-editor/editor.css'
import './builder.css'

const node_types = { module: ModuleNode }
const empty_actions = { toggle_lock: (_module_id: string) => {} }

interface Props {
  definition: MacroDefinition
  module_types: ModuleType[]
  selected_block_id: string | null
  disabled?: boolean
  on_select: (block_id: string | null) => void
  on_drop_block: (module_type: ModuleType, position: { x: number; y: number }) => void
  on_move_block: (block_id: string, position: { x: number; y: number }) => void
  on_remove_blocks: (block_ids: string[]) => void
  on_connect: (connection: Connection) => void
  on_remove_connections: (connections: ReturnType<typeof connection_from_edge>[]) => void
}

function BuilderFlow(props: Props) {
  const { screenToFlowPosition } = useReactFlow()
  const [selected_edge_ids, set_selected_edge_ids] = useState<Set<string>>(() => new Set())
  const flow = useMemo(() => definition_to_flow(props.definition, props.module_types), [props.definition, props.module_types])
  const nodes = useMemo(() => flow.nodes.map(node => ({ ...node, selected: node.id === props.selected_block_id })), [flow.nodes, props.selected_block_id])
  const edges = useMemo(() => flow.edges.map(edge => ({ ...edge, selected: selected_edge_ids.has(edge.id) })), [flow.edges, selected_edge_ids])
  const type_by_id = useMemo(() => new Map(props.module_types.map(module_type => [module_type.id, module_type])), [props.module_types])

  const on_drop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    if (props.disabled) return
    const type_id = event.dataTransfer.getData(drag_mime)
    const module_type = props.module_types.find(item => item.id === type_id)
    if (!module_type) return
    props.on_drop_block(module_type, screenToFlowPosition({ x: event.clientX, y: event.clientY }))
  }

  const can_connect = (connection: Connection | Edge) => {
    if (props.disabled || !connection.source || !connection.target || !connection.sourceHandle || connection.targetHandle !== 'in') return false
    const source = nodes.find(node => node.id === connection.source)
    const target = nodes.find(node => node.id === connection.target)
    if (!source || !target || source.id === target.id || !type_by_id.get(source.data.module.type_id)?.outputs.some(output => output.id === connection.sourceHandle)) return false
    if (!type_by_id.get(target.data.module.type_id)?.has_input) return false
    return is_valid_connection(connection, new Set(), flow.edges)
  }

  const on_nodes_change = (changes: NodeChange<ModuleFlowNode>[]) => {
    for (const change of changes) {
      if (change.type === 'select' && change.selected) props.on_select(change.id)
      if (change.type === 'position' && change.position && !props.disabled) props.on_move_block(change.id, change.position)
    }
  }

  const on_edges_change = (changes: EdgeChange[]) => {
    const selected = changes.filter(change => change.type === 'select')
    if (selected.length === 0) return
    set_selected_edge_ids(current => {
      const next = new Set(current)
      for (const change of selected) {
        if (change.selected) next.add(change.id)
        else next.delete(change.id)
      }
      return next
    })
  }
  const on_edges_delete = (deleted: Edge[]) => {
    set_selected_edge_ids(current => {
      const next = new Set(current)
      for (const edge of deleted) next.delete(edge.id)
      return next
    })
    props.on_remove_connections(deleted.map(connection_from_edge))
  }

  return (
    <EditorModeContext.Provider value="edit">
      <EditorActionsContext.Provider value={empty_actions}>
        <div className="module-builder-body">
          <aside className="module-builder-left">
            <div className="module-builder-left-title"><Icon name="blocks" size={16} /><span>Bausteine</span></div>
            <ModulePalette module_types={props.module_types} placeholder="Bausteine suchen …" empty_text="Keine Bausteine vorhanden" />
          </aside>
          <div className="module-builder-canvas workflow-editor-canvas" onDragOver={event => { event.preventDefault(); event.dataTransfer.dropEffect = 'move' }} onDrop={on_drop}>
            <ReactFlow
              nodes={nodes}
              edges={edges}
              nodeTypes={node_types}
              onNodesChange={on_nodes_change}
              onNodesDelete={deleted => props.on_remove_blocks(deleted.map(node => node.id))}
              onEdgesChange={on_edges_change}
              onEdgesDelete={on_edges_delete}
              onConnect={connection => { if (can_connect(connection)) props.on_connect(connection) }}
              isValidConnection={can_connect}
              onNodeClick={(_event, node) => props.on_select(node.id)}
              onPaneClick={() => props.on_select(null)}
              nodesDraggable={!props.disabled}
              nodesConnectable={!props.disabled}
              elementsSelectable={!props.disabled}
              deleteKeyCode={props.disabled ? null : ['Delete', 'Backspace']}
              fitView
              fitViewOptions={{ padding: 0.25 }}
            >
              <Background variant={BackgroundVariant.Dots} gap={20} size={1} />
              <Controls />
            </ReactFlow>
          </div>
        </div>
      </EditorActionsContext.Provider>
    </EditorModeContext.Provider>
  )
}

export function BuilderCanvas(props: Props) {
  return <ReactFlowProvider><BuilderFlow {...props} /></ReactFlowProvider>
}
