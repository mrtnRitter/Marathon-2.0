import { useCallback, useState, type DragEvent } from 'react'
import {
  Background, BackgroundVariant, Controls, MiniMap, ReactFlow, ReactFlowProvider, addEdge, useEdgesState, useNodesState, useReactFlow,
  type Connection, type Edge, type IsValidConnection, type NodeMouseHandler,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { Button } from '../../components/Toolbar'
import { not_available } from '../../components/Toast'
import { get_module_types, get_workflow_graph } from '../../data/api'
import type { ModuleType, WorkflowGraph, WorkflowModule } from '../../data/types'
import { use_data } from '../../data/use_data'
import { EditorModeContext, drag_mime, type EditorMode, type ModuleFlowNode, type ModuleNodeData } from './editor_types'
import { create_edge, create_node, new_module, to_edges, to_nodes } from './graph_mapping'
import { ModuleActivityPanel } from './ModuleActivityPanel'
import { ModuleNode } from './ModuleNode'
import { ModulePalette } from './ModulePalette'
import { ModuleSettingsPanel } from './ModuleSettingsPanel'
import './editor.css'

const node_types = { module: ModuleNode }

const minimap_color = (node: ModuleFlowNode) =>
  node.data.module.counters.failed > 0 ? 'var(--status-failed)' : node.data.module.counters.running > 0 ? 'var(--status-in-progress)' : 'var(--border-strong)'

export function WorkflowCanvas({ workflow_id }: { workflow_id: string }) {
  const { data } = use_data(() => Promise.all([get_workflow_graph(workflow_id), get_module_types()]), [workflow_id])
  if (!data) return <div className="loading">Lade Workflow</div>
  const [graph, module_types] = data
  if (!graph) return <div className="loading">Kein Graph für diesen Workflow</div>
  return (
    <ReactFlowProvider>
      <Editor key={workflow_id} graph={graph} module_types={module_types} />
    </ReactFlowProvider>
  )
}

function Editor({ graph, module_types }: { graph: WorkflowGraph; module_types: ModuleType[] }) {
  const [mode, set_mode] = useState<EditorMode>('monitor')
  const [nodes, set_nodes, on_nodes_change] = useNodesState<ModuleFlowNode>(to_nodes(graph, module_types))
  const [edges, set_edges, on_edges_change] = useEdgesState<Edge>(to_edges(graph))
  const [dirty, set_dirty] = useState(false)
  const [settings_node, set_settings_node] = useState<ModuleNodeData | null>(null)
  const [activity_node, set_activity_node] = useState<ModuleNodeData | null>(null)
  const { screenToFlowPosition } = useReactFlow()
  const editing = mode === 'edit'

  const reset = () => {
    set_nodes(to_nodes(graph, module_types))
    set_edges(to_edges(graph))
    set_dirty(false)
  }

  // Each output carries at most one connection, an input accepts many
  const is_valid_connection: IsValidConnection = connection =>
    connection.source !== connection.target && !edges.some(edge => edge.source === connection.source && edge.sourceHandle === connection.sourceHandle)

  const on_connect = useCallback((connection: Connection) => {
    set_edges(current => addEdge(create_edge(connection), current))
    set_dirty(true)
  }, [set_edges])

  const on_drop = (event: DragEvent) => {
    event.preventDefault()
    const module_type = module_types.find(type => type.id === event.dataTransfer.getData(drag_mime))
    if (!module_type) return
    const position = screenToFlowPosition({ x: event.clientX, y: event.clientY })
    set_nodes(current => [...current, create_node(new_module(module_type, position, current.length), module_type)])
    set_dirty(true)
  }

  const apply_settings = (module: WorkflowModule) => {
    set_nodes(current => current.map(node => (node.id === module.id ? { ...node, data: { ...node.data, module } } : node)))
    set_dirty(true)
  }

  const on_node_click: NodeMouseHandler<ModuleFlowNode> = (_, node) => !editing && set_activity_node(node.data)
  const on_node_double_click: NodeMouseHandler<ModuleFlowNode> = (_, node) => editing && set_settings_node(node.data)

  return (
    <EditorModeContext.Provider value={mode}>
      <div className="workflow-editor">
        <div className="workflow-editor-bar">
          <div className="mode-switch" role="tablist">
            {(['monitor', 'edit'] as const).map(item => (
              <button key={item} type="button" role="tab" aria-selected={mode === item} className={mode === item ? 'active' : undefined} onClick={() => set_mode(item)}>
                {item === 'monitor' ? 'Überwachen' : 'Bearbeiten'}
              </button>
            ))}
          </div>
          <span className="dim">Version {graph.version} · {nodes.length} Module · {edges.length} Verbindungen</span>
          {dirty && <span className="chip unsaved">ungespeichert</span>}
          <div className="workflow-editor-actions">
            {editing ? (
              <>
                <Button label="Verwerfen" icon="undo" on_click={reset} disabled={!dirty} />
                <Button label="Speichern" icon="save" primary on_click={not_available} />
              </>
            ) : <span className="live-indicator">live · Beispieldaten</span>}
          </div>
        </div>
        <div className="workflow-editor-body">
          <div className="workflow-editor-canvas" onDragOver={event => { event.preventDefault(); event.dataTransfer.dropEffect = 'move' }} onDrop={editing ? on_drop : undefined}>
            <ReactFlow
              colorMode="dark"
              nodes={nodes}
              edges={edges}
              nodeTypes={node_types}
              onNodesChange={changes => { on_nodes_change(changes); if (editing && changes.some(change => change.type !== 'select' && change.type !== 'dimensions')) set_dirty(true) }}
              onEdgesChange={changes => { on_edges_change(changes); if (changes.some(change => change.type === 'remove')) set_dirty(true) }}
              onConnect={on_connect}
              isValidConnection={is_valid_connection}
              onNodeClick={on_node_click}
              onNodeDoubleClick={on_node_double_click}
              nodesDraggable={editing}
              nodesConnectable={editing}
              edgesFocusable={editing}
              deleteKeyCode={editing ? ['Delete', 'Backspace'] : null}
              zoomOnDoubleClick={false}
              fitView
              fitViewOptions={{ padding: 0.25 }}
              minZoom={0.3}
              maxZoom={1.6}
            >
              <Background variant={BackgroundVariant.Dots} gap={18} size={1.2} />
              <Controls showInteractive={false} />
              <MiniMap<ModuleFlowNode> pannable zoomable nodeColor={minimap_color} nodeBorderRadius={4} />
            </ReactFlow>
          </div>
          {editing && <ModulePalette module_types={module_types} />}
        </div>
      </div>
      <ModuleSettingsPanel data={settings_node} on_close={() => set_settings_node(null)} on_apply={apply_settings} />
      {activity_node && <ModuleActivityPanel workflow_id={graph.workflow_id} data={activity_node} on_close={() => set_activity_node(null)} />}
    </EditorModeContext.Provider>
  )
}
