import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import type { Connection } from '@xyflow/react'
import { EditLockNotice } from '../components/EditLockNotice'
import { Icon } from '../components/Icon'
import { PageHeader } from '../components/PageHeader'
import { ShareDialog } from '../components/ShareDialog'
import { SlidePanel } from '../components/SlidePanel'
import { notify } from '../components/Toast'
import { use_auth_user } from '../data/auth'
import { ApiError, create_macro, get_macro, get_macros, get_module_types, update_macro, update_macro_workflows } from '../data/api'
import type { Access, MacroDefinition, MacroDetail, MacroWorkflowRef, ModuleType } from '../data/types'
import { use_data } from '../data/use_data'
import { use_edit_lock } from '../data/use_edit_lock'
import { add_block_to_definition, connection_from_edge, remove_connections_from_definition } from '../features/module-builder/builder_mapping'
import { BlockPanel } from '../features/module-builder/BlockPanel'
import { BuilderCanvas } from '../features/module-builder/BuilderCanvas'
import { MacroPanel } from '../features/module-builder/MacroPanel'
import { has_access, is_lock_conflict } from '../lib/access'
import { new_definition, prune_definition } from '../lib/macro_builder'
import { lock_held_prefix } from '../lib/edit_lock'
import type { EditLockState } from '../lib/edit_lock'

interface BuilderLoad {
  module_types: ModuleType[]
  macro: MacroDetail | null
  macro_access: Access | undefined
  forbidden: boolean
}

async function load_builder(macro_id?: string): Promise<BuilderLoad> {
  const module_types_request = get_module_types()
  if (!macro_id) return { module_types: await module_types_request, macro: null, macro_access: undefined, forbidden: false }
  const macro_request = get_macro(macro_id).then(macro => ({ macro, forbidden: false })).catch(error => {
    if (error instanceof ApiError && error.status === 403) return { macro: null, forbidden: true }
    throw error
  })
  const [module_types, result] = await Promise.all([module_types_request, macro_request])
  let macro_access = result.macro?.access
  if (result.macro && macro_access == null) {
    macro_access = (await get_macros()).find(macro => macro.id === macro_id)?.access
  }
  return { module_types, ...result, macro_access }
}

export function ModuleBuilderPage() {
  const { macro_id } = useParams()
  const { data, loading, error, reload } = use_data(() => load_builder(macro_id), [macro_id])
  if (loading) return <div className="loading">Lade Modul</div>
  if (!data) return <div className="page-content"><p>{error ?? 'Modul konnte nicht geladen werden.'}</p></div>
  if (data.forbidden) return <div className="page-content"><p>Keine Berechtigung für dieses Modul</p></div>
  if (macro_id && !data.macro) return <div className="page-content"><p>Modul nicht gefunden</p></div>
  return (
    <BuilderEditor
      key={macro_id ?? 'new'}
      macro_id={macro_id}
      initial_definition={data.macro?.definition ?? new_definition()}
      initial_version={data.macro?.version}
      macro_access={data.macro_access}
      module_types={data.module_types}
      reload={reload}
    />
  )
}

function BuilderEditor({ macro_id, initial_definition, initial_version, macro_access, module_types, reload }: {
  macro_id?: string
  initial_definition: MacroDefinition
  initial_version?: number
  macro_access: Access | undefined
  module_types: ModuleType[]
  reload: () => void
}) {
  const navigate = useNavigate()
  const edit_lock = use_edit_lock('macro', macro_id ?? null, Boolean(macro_id))
  const [lock_state_override, set_lock_state_override] = useState<EditLockState | null>(null)
  const lock_state = lock_state_override ?? edit_lock.state
  const read_only = Boolean(macro_id) && lock_state.status !== 'held'
  const is_admin = use_auth_user()?.role === 'admin'
  const [sharing, set_sharing] = useState(false)
  const [definition, set_definition] = useState(() => structuredClone(initial_definition))
  const [base_version, set_base_version] = useState(initial_version)
  const [selected_block_id, set_selected_block_id] = useState<string | null>(null)
  const [dirty, set_dirty] = useState(false)
  const [saving, set_saving] = useState(false)
  const [outdated_workflows, set_outdated_workflows] = useState<MacroWorkflowRef[]>([])
  const [force_update_available, set_force_update_available] = useState(false)
  const [updating_workflows, set_updating_workflows] = useState(false)

  const commit_definition = (next: MacroDefinition | ((current: MacroDefinition) => MacroDefinition)) => {
    if (saving || read_only) return
    set_definition(current => prune_definition(typeof next === 'function' ? next(current) : next, module_types))
    set_dirty(true)
  }

  useEffect(() => {
    if (!dirty) return
    const warn_before_unload = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', warn_before_unload)
    return () => window.removeEventListener('beforeunload', warn_before_unload)
  }, [dirty])

  const go_back = () => {
    if (dirty && !window.confirm('Ungespeicherte Änderungen verwerfen?')) return
    navigate('/modules')
  }

  const add_block = (module_type: ModuleType, position: { x: number; y: number }) => {
    const next = add_block_to_definition(definition, module_type, module_types, position)
    const added = next.blocks.at(-1)
    commit_definition(next)
    if (added && !read_only) set_selected_block_id(added.id)
  }

  const move_block = (block_id: string, position: { x: number; y: number }) => commit_definition(current => ({
    ...current,
    blocks: current.blocks.map(block => block.id === block_id ? { ...block, position: { ...position } } : block),
  }))

  const remove_blocks = (block_ids: string[]) => {
    const removed = new Set(block_ids)
    commit_definition(current => ({
      ...current,
      blocks: current.blocks.filter(block => !removed.has(block.id)),
      entry_block_id: current.entry_block_id && removed.has(current.entry_block_id) ? null : current.entry_block_id,
    }))
    if (selected_block_id && removed.has(selected_block_id) && !read_only) set_selected_block_id(null)
  }

  const remove_connections = (connections: ReturnType<typeof connection_from_edge>[]) => {
    commit_definition(current => remove_connections_from_definition(current, connections))
  }

  const connect_blocks = (connection: Connection) => {
    if (!connection.source || !connection.target || !connection.sourceHandle || read_only) return
    const next_connection = connection_from_edge(connection)
    commit_definition(current => current.connections.some(item => item.source_block_id === next_connection.source_block_id && item.source_output_id === next_connection.source_output_id)
      ? current
      : { ...current, connections: [...current.connections, next_connection] })
  }

  const save = async () => {
    if (saving || read_only) return
    set_saving(true)
    try {
      if (!macro_id) {
        const created = await create_macro(definition)
        set_dirty(false)
        notify('Gespeichert')
        navigate(`/modules/${encodeURIComponent(created.id)}`, { replace: true })
        return
      }
      if (base_version === undefined) return
      const saved = await update_macro(macro_id, base_version, definition, lock_state.token ?? undefined)
      set_definition(prune_definition(saved.definition, module_types))
      set_base_version(saved.version)
      set_dirty(false)
      notify('Gespeichert')
      set_outdated_workflows(saved.outdated_workflows)
      set_force_update_available(false)
    } catch (error) {
      if (macro_id && error instanceof ApiError && error.status === 409 && is_lock_conflict(error.detail)) {
        set_lock_state_override({ status: 'lost', token: null, expires_at: null, message: error.detail, conflict: error.detail.startsWith(lock_held_prefix) })
      }
      // The request layer already reports save failures.
    } finally {
      set_saving(false)
    }
  }

  const update_workflows = async (force = false) => {
    if (!macro_id || updating_workflows) return
    set_updating_workflows(true)
    try {
      const result = force
        ? await update_macro_workflows(macro_id, true)
        : await update_macro_workflows(macro_id)
      const count = result.updated.length
      notify(count === 1 ? '1 Workflow aktualisiert' : `${count} Workflows aktualisiert`)
      for (const skipped of result.skipped) notify(`${skipped.name}: ${skipped.reason}`)
      const locked_skips = result.skipped.filter(skipped => skipped.reason.startsWith(lock_held_prefix))
      if (is_admin && locked_skips.length > 0) {
        set_outdated_workflows(locked_skips.map(({ id, name }) => ({ id, name })))
        set_force_update_available(true)
      } else {
        set_outdated_workflows([])
        set_force_update_available(false)
      }
    } catch {
      // The request layer already reports update failures.
    } finally {
      set_updating_workflows(false)
    }
  }

  const selected_block = definition.blocks.find(block => block.id === selected_block_id)
  const selected_type = selected_block ? module_types.find(type => type.id === selected_block.type_id) : undefined
  const workflow_update_text = outdated_workflows.length === 1
    ? '1 eigener Workflow verwendet eine ältere Version dieses Moduls.'
    : `${outdated_workflows.length} eigene Workflows verwenden eine ältere Version dieses Moduls.`
  const share_action = macro_id && has_access(macro_access, 'owner')
    ? [{ label: 'Teilen', icon: 'users' as const, on_click: () => set_sharing(true) }]
    : []

  return (
    <>
      <PageHeader
        title={<>{definition.name}{base_version !== undefined && <span className="dim"> · v{base_version}</span>}</>}
        actions={[
          ...share_action,
          { label: 'Speichern', icon: 'save', primary: true, on_click: () => { void save() }, disabled: saving || read_only },
          { label: 'Zurück', icon: 'arrow-left', on_click: go_back, disabled: saving },
        ]}
      />
      {macro_id && <EditLockNotice object_type="macro" object_id={macro_id} state={lock_state} on_retry={edit_lock.retry} />}
      <div className="page-content flush module-builder-page">
        <div className="workflow-editor module-builder-editor">
          <BuilderCanvas
            definition={definition}
            module_types={module_types}
            selected_block_id={selected_block_id}
            disabled={saving || read_only}
            on_select={set_selected_block_id}
            on_drop_block={add_block}
            on_move_block={move_block}
            on_remove_blocks={remove_blocks}
            on_connect={connect_blocks}
            on_remove_connections={remove_connections}
          />
          <aside className="module-builder-panel">
            <div className="module-builder-panel-header">
              {selected_block && selected_type ? <><span className="module-node-icon"><Icon name={selected_type.icon} size={14} /></span><span>{selected_block.name}</span></> : <span>Modul</span>}
            </div>
            {selected_block && selected_type
              ? <BlockPanel key={selected_block.id} block={selected_block} module_type={selected_type} module_types={module_types} definition={definition} disabled={saving || read_only} on_definition_change={commit_definition} />
              : <MacroPanel definition={definition} module_types={module_types} disabled={saving || read_only} on_definition_change={commit_definition} />}
          </aside>
        </div>
      </div>
      <SlidePanel
        open={outdated_workflows.length > 0}
        title="Workflows aktualisieren"
        icon="reprocess"
        on_close={() => { set_outdated_workflows([]); set_force_update_available(false) }}
        actions={[
          { label: 'Später', icon: 'clock', on_click: () => { set_outdated_workflows([]); set_force_update_available(false) }, disabled: updating_workflows },
          { label: 'Alle aktualisieren', icon: 'reprocess', primary: true, on_click: () => { void update_workflows() }, disabled: updating_workflows },
          ...(force_update_available ? [{ label: 'Gesperrte trotzdem aktualisieren', icon: 'reprocess' as const, on_click: () => { void update_workflows(true) }, disabled: updating_workflows }] : []),
        ]}
      >
        <p>{workflow_update_text}</p>
        <ul>{outdated_workflows.map(workflow => <li key={workflow.id}>{workflow.name}</li>)}</ul>
      </SlidePanel>
      {sharing && macro_id && <ShareDialog object_type="macro" object_id={macro_id} on_close={() => set_sharing(false)} on_changed={() => { void reload() }} />}
    </>
  )
}
