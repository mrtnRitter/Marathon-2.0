import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent } from 'react'
import {
  Background, BackgroundVariant, Controls, MiniMap, ReactFlow, ReactFlowProvider, SelectionMode, addEdge, useEdgesState, useNodesState, useReactFlow, useStore, useStoreApi,
  type Connection, type Edge, type IsValidConnection, type NodeMouseHandler, type OnBeforeDelete,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { Button } from '../../components/Toolbar'
import { notify } from '../../components/Toast'
import { get_module_types, get_workflow_graph, save_workflow_graph } from '../../data/api'
import type { ModuleType, WorkflowGraph, WorkflowModule } from '../../data/types'
import { has_setting_errors } from '../../lib/module_settings'
import { use_data } from '../../data/use_data'
import { EditorActionsContext, EditorModeContext, drag_mime, type EditorMode, type ModuleFlowNode, type ModuleNodeData } from './editor_types'
import { apply_lock, create_edge, create_node, locked_ids, lock_edges, lock_targets, new_module, to_edges, to_graph_input, to_nodes, view_nodes } from './graph_mapping'
import { ModuleActivityPanel } from './ModuleActivityPanel'
import { ModuleNode } from './ModuleNode'
import { ModulePalette } from './ModulePalette'
import { covered_node_ids, ignore_select_changes, to_flow_rect } from './selection'
import { ModuleSettingsPanel } from './ModuleSettingsPanel'
import './editor.css'

const node_types = { module: ModuleNode }

const settings_width_key = 'marathon.editor.settings_width'
const settings_width_range = { min: 260, max: 640, initial: 320 }
const clamp_settings_width = (width: number) => Math.min(settings_width_range.max, Math.max(settings_width_range.min, Math.round(width)))

function read_settings_width(): number {
  try {
    const value = Number(localStorage.getItem(settings_width_key))
    return value ? clamp_settings_width(value) : settings_width_range.initial
  } catch {
    return settings_width_range.initial
  }
}

const minimap_color = (node: ModuleFlowNode) =>
  node.data.module.counters.failed > 0 ? 'var(--status-failed)' : node.data.module.counters.running > 0 ? 'var(--status-in-progress)' : 'var(--border-strong)'

export function WorkflowCanvas({ workflow_id, initial_mode }: { workflow_id: string; initial_mode?: EditorMode }) {
  const [mode, set_mode] = useState<EditorMode>(initial_mode ?? 'monitor')
  const { data, reload } = use_data(() => Promise.all([get_workflow_graph(workflow_id), get_module_types()]), [workflow_id])
  if (!data) return <div className="loading">Lade Workflow</div>
  const [graph, module_types] = data
  if (!graph) return <div className="loading">Kein Graph für diesen Workflow</div>
  return (
    <ReactFlowProvider>
      <Editor key={`${workflow_id}-${graph.version}`} graph={graph} module_types={module_types} mode={mode} on_mode_change={set_mode} on_saved={reload} />
    </ReactFlowProvider>
  )
}

function Editor({ graph, module_types, mode, on_mode_change, on_saved }: { graph: WorkflowGraph; module_types: ModuleType[]; mode: EditorMode; on_mode_change: (mode: EditorMode) => void; on_saved: () => void }) {
  const [settings_width, set_settings_width] = useState(read_settings_width)
  useEffect(() => {
    try { localStorage.setItem(settings_width_key, String(settings_width)) } catch {}
  }, [settings_width])
  const initial_nodes = to_nodes(graph, module_types)
  const [nodes, set_nodes, on_nodes_change] = useNodesState<ModuleFlowNode>(initial_nodes)
  const locked_module_ids = useRef(locked_ids(initial_nodes))
  const [edges, set_edges, on_edges_change] = useEdgesState<Edge>(lock_edges(to_edges(graph), locked_ids(initial_nodes)))
  const [dirty, set_dirty] = useState(false)
  const [saving, set_saving] = useState(false)
  const [activity_node, set_activity_node] = useState<ModuleNodeData | null>(null)
  const [inspected_id, set_inspected_id] = useState<string | null>(null)
  const { screenToFlowPosition } = useReactFlow()
  const store = useStoreApi<ModuleFlowNode, Edge>()
  const selection_rect = useStore(state => state.userSelectionRect)
  const transform = useStore(state => state.transform)
  const editing = mode === 'edit'

  useEffect(() => {
    if (!(editing && selection_rect && (selection_rect.width > 0 || selection_rect.height > 0))) return
    const ids = covered_node_ids(Array.from(store.getState().nodeLookup.values(), node => ({
      id: node.id,
      selectable: node.selectable,
      hidden: node.hidden,
      rect: {
        x: node.internals.positionAbsolute.x,
        y: node.internals.positionAbsolute.y,
        width: node.measured.width ?? 0,
        height: node.measured.height ?? 0,
      },
    })), to_flow_rect(selection_rect, transform))
    set_nodes(current => current.map(node => {
      const selected = ids.has(node.id)
      return Boolean(node.selected) === selected ? node : { ...node, selected }
    }))
    set_edges(current => current.map(edge => {
      const selected = edge.selectable !== false && (ids.has(edge.source) || ids.has(edge.target))
      return Boolean(edge.selected) === selected ? edge : { ...edge, selected }
    }))
  }, [selection_rect, transform, editing])
  const display_nodes = useMemo(() => view_nodes(nodes, editing), [nodes, editing])
  const selected_nodes = nodes.filter(node => node.selected)
  const invalid = nodes.filter(node => has_setting_errors(node.data.module_type, node.data.module.settings))
  const inspected_node = nodes.find(node => node.id === inspected_id && node.data.module.locked)

  const save = async () => {
    if (saving || invalid.length > 0) return
    set_saving(true)
    try {
      await save_workflow_graph(graph.workflow_id, to_graph_input(graph.version, nodes, edges))
      notify('Gespeichert')
      on_saved()
    } catch {
      // The request already displays the error.
    } finally {
      set_saving(false)
    }
  }

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
    const { ids, locked } = lock_targets(nodes, module_id)
    if (ids.length === 0) return
    const target_ids = new Set(ids)
    const next_locked_ids = new Set(locked_module_ids.current)
    for (const id of ids) {
      if (locked) next_locked_ids.add(id)
      else next_locked_ids.delete(id)
    }
    locked_module_ids.current = next_locked_ids
    set_nodes(current => current.map(node => target_ids.has(node.id)
      ? apply_lock({ ...node, selected: false, data: { ...node.data, module: { ...node.data.module, locked } } })
      : node))
    set_edges(current => lock_edges(current, next_locked_ids))
    if (!locked) set_inspected_id(current => target_ids.has(current ?? '') ? null : current)
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
                <button key={item} type="button" role="tab" aria-selected={mode === item} className={mode === item ? 'active' : undefined} onClick={() => { on_mode_change(item); set_inspected_id(null) }}>
                  {item === 'monitor' ? 'Überwachen' : 'Bearbeiten'}
                </button>
              ))}
            </div>
            <span className="dim">{nodes.length} Module · {edges.length} Verbindungen</span>
            {dirty && <span className="chip unsaved">ungespeichert</span>}
            {editing && invalid.length > 0 && <span className="chip invalid">Einstellungen prüfen: {invalid.map(node => node.data.module.name).join(', ')}</span>}
            <div className="workflow-editor-actions">
              {editing ? (
                <>
                  <Button label="Verwerfen" icon="undo" on_click={reset} disabled={!dirty} />
                  <Button label="Speichern" icon="save" primary on_click={save} disabled={!dirty || saving || invalid.length > 0} />
                </>
              ) : null}
            </div>
          </div>
          <div className="workflow-editor-body">
            {editing && <ModulePalette module_types={module_types} />}
            <div className="workflow-editor-canvas" onDragOver={event => { event.preventDefault(); event.dataTransfer.dropEffect = 'move' }} onDrop={editing ? on_drop : undefined}>
              <ReactFlow
                colorMode="dark"
                nodes={display_nodes}
                edges={edges}
                nodeTypes={node_types}
                onNodesChange={changes => {
                  const filtered_changes = ignore_select_changes(store.getState().userSelectionRect) ? changes.filter(change => change.type !== 'select') : changes
                  on_nodes_change(filtered_changes)
                  if (editing && filtered_changes.some(change => change.type !== 'select' && change.type !== 'dimensions')) set_dirty(true)
                }}
                onEdgesChange={changes => {
                  const filtered_changes = ignore_select_changes(store.getState().userSelectionRect) ? changes.filter(change => change.type !== 'select') : changes
                  on_edges_change(filtered_changes)
                  if (filtered_changes.some(change => change.type === 'remove')) set_dirty(true)
                }}
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
              <ModuleSettingsPanel key={selected_nodes[0].id} data={selected_nodes[0].data} on_change={apply_settings} width={settings_width} on_width_change={width => set_settings_width(clamp_settings_width(width))} />
            )}
            {editing && selected_nodes.length === 0 && inspected_node && (
              <ModuleSettingsPanel key={inspected_node.id} data={inspected_node.data} on_change={apply_settings} width={settings_width} on_width_change={width => set_settings_width(clamp_settings_width(width))} read_only />
            )}
          </div>
        </div>
        {activity_node && <ModuleActivityPanel workflow_id={graph.workflow_id} data={activity_node} on_close={() => set_activity_node(null)} />}
      </EditorModeContext.Provider>
    </EditorActionsContext.Provider>
  )
}
