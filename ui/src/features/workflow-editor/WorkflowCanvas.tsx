import { useCallback, useRef, useState, type DragEvent } from 'react'
import {
  Background, BackgroundVariant, Controls, MiniMap, ReactFlow, ReactFlowProvider, SelectionMode, addEdge, useEdgesState, useNodesState, useReactFlow,
  type Connection, type Edge, type IsValidConnection, type NodeMouseHandler, type OnBeforeDelete,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { Button } from '../../components/Toolbar'
import { not_available, notify } from '../../components/Toast'
import { get_module_types, get_workflow_graph } from '../../data/api'
import type { ModuleType, WorkflowGraph, WorkflowModule } from '../../data/types'
import { use_data } from '../../data/use_data'
import { EditorActionsContext, EditorModeContext, drag_mime, type EditorMode, type ModuleFlowNode, type ModuleNodeData } from './editor_types'
import { apply_lock, create_edge, create_node, locked_ids, lock_edges, new_module, to_edges, to_nodes } from './graph_mapping'
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
  const initial_nodes = to_nodes(graph, module_types)
  const [nodes, set_nodes, on_nodes_change] = useNodesState<ModuleFlowNode>(initial_nodes)
  const locked_module_ids = useRef(locked_ids(initial_nodes))
  const [edges, set_edges, on_edges_change] = useEdgesState<Edge>(lock_edges(to_edges(graph), locked_ids(initial_nodes)))
  const [dirty, set_dirty] = useState(false)
  const [activity_node, set_activity_node] = useState<ModuleNodeData | null>(null)
  const [inspected_id, set_inspected_id] = useState<string | null>(null)
  const { screenToFlowPosition } = useReactFlow()
  const editing = mode === 'edit'
  const selected_nodes = nodes.filter(node => node.selected)
  const inspected_node = nodes.find(node => node.id === inspected_id && node.data.module.locked)

  const reset = () => {
    const reset_nodes = to_nodes(graph, module_types)
    const reset_locked_ids = locked_ids(reset_nodes)
    locked_module_ids.current = reset_locked_ids
    set_nodes(reset_nodes)
    set_edges(lock_edges(to_edges(graph), reset_locked_ids))
    set_inspected_id(null)
    set_dirty(false)
  }

  const toggle_lock = (module_id: string) => {
    if (!nodes.some(node => node.id === module_id)) return
    const next_locked_ids = new Set(locked_module_ids.current)
    if (next_locked_ids.has(module_id)) next_locked_ids.delete(module_id)
    else next_locked_ids.add(module_id)
    locked_module_ids.current = next_locked_ids
    const locked = next_locked_ids.has(module_id)
    set_nodes(current => current.map(node => node.id === module_id
      ? apply_lock({ ...node, selected: false, data: { ...node.data, module: { ...node.data.module, locked } } })
      : node))
    set_edges(current => lock_edges(current, next_locked_ids))
    if (!locked) set_inspected_id(current => current === module_id ? null : current)
    set_dirty(true)
  }

  // Each output carries at most one connection, an input accepts many
  const is_valid_connection: IsValidConnection = connection => {
    const current_locked_ids = locked_ids(nodes)
    return connection.source !== connection.target
      && !current_locked_ids.has(connection.source)
      && !current_locked_ids.has(connection.target)
      && !edges.some(edge => edge.source === connection.source && edge.sourceHandle === connection.sourceHandle)
  }

  const on_connect = useCallback((connection: Connection) => {
    set_edges(current => addEdge(create_edge(connection), current))
    set_dirty(true)
  }, [set_edges])

  const on_before_delete: OnBeforeDelete<ModuleFlowNode, Edge> = async ({ nodes: delete_nodes, edges: delete_edges }) => {
    const current_locked_ids = locked_ids(nodes)
    const adjacent_to_locked = new Set(edges.flatMap(edge => {
      if (current_locked_ids.has(edge.source)) return [edge.target]
      if (current_locked_ids.has(edge.target)) return [edge.source]
      return []
    }))
    const safe_nodes = delete_nodes.filter(node => !current_locked_ids.has(node.id) && !adjacent_to_locked.has(node.id))
    const safe_edges = delete_edges.filter(edge => !current_locked_ids.has(edge.source) && !current_locked_ids.has(edge.target))
    if (safe_nodes.length !== delete_nodes.length || safe_edges.length !== delete_edges.length) {
      notify('Module mit Verbindung zu gesperrten Modulen werden nicht gelöscht')
    }
    if (safe_nodes.length === 0 && safe_edges.length === 0) return false
    return { nodes: safe_nodes, edges: safe_edges }
  }

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

  const on_node_click: NodeMouseHandler<ModuleFlowNode> = (_, node) => {
    if (!editing) set_activity_node(node.data)
    else if (node.data.module.locked) set_inspected_id(node.id)
  }

  return (
    <EditorActionsContext.Provider value={{ toggle_lock }}>
      <EditorModeContext.Provider value={mode}>
        <div className="workflow-editor">
          <div className="workflow-editor-bar">
            <div className="mode-switch" role="tablist">
              {(['monitor', 'edit'] as const).map(item => (
                <button key={item} type="button" role="tab" aria-selected={mode === item} className={mode === item ? 'active' : undefined} onClick={() => { set_mode(item); set_inspected_id(null) }}>
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
            {editing && <ModulePalette module_types={module_types} />}
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
                onPaneClick={() => set_inspected_id(null)}
                onSelectionChange={({ nodes: selection }) => { if (selection.length > 0) set_inspected_id(null) }}
                onBeforeDelete={on_before_delete}
                nodesDraggable={editing}
                nodesConnectable={editing}
                edgesFocusable={editing}
                deleteKeyCode={editing ? ['Delete', 'Backspace'] : null}
                selectionOnDrag={editing}
                panOnDrag={editing ? [1] : true}
                selectionMode={SelectionMode.Full}
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
            {editing && selected_nodes.length === 1 && (
              <ModuleSettingsPanel key={selected_nodes[0].id} data={selected_nodes[0].data} on_change={apply_settings} />
            )}
            {editing && selected_nodes.length === 0 && inspected_node && (
              <ModuleSettingsPanel key={inspected_node.id} data={inspected_node.data} on_change={apply_settings} read_only />
            )}
          </div>
        </div>
        {activity_node && <ModuleActivityPanel workflow_id={graph.workflow_id} data={activity_node} on_close={() => set_activity_node(null)} />}
      </EditorModeContext.Provider>
    </EditorActionsContext.Provider>
  )
}
