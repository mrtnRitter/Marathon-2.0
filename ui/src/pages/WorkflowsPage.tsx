import { useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { DataTable, type Column } from '../components/DataTable'
import { FormField } from '../components/FormField'
import { AutoTextarea } from '../components/AutoTextarea'
import { Icon } from '../components/Icon'
import { PageHeader } from '../components/PageHeader'
import { SlidePanel } from '../components/SlidePanel'
import { ShareDialog } from '../components/ShareDialog'
import { StatusBadge } from '../components/StatusBadge'
import { IssueMark } from '../components/IssueMark'
import { StatusBar } from '../components/StatusBar'
import { ScheduleFields } from '../features/workflows/ScheduleFields'
import { notify } from '../components/Toast'
import { ApiError, create_workflow, delete_workflow, duplicate_workflow, get_workflows, update_workflow, workflow_name_taken_detail } from '../data/api'
import type { FieldSpec, Workflow, WorkflowAccess, WorkflowInput } from '../data/types'
import { schedule_error, schedule_label } from '../lib/schedule'
import { format_datetime } from '../lib/format'
import { name_taken, set_workflows_active, use_pausing_notice } from '../lib/workflows'
import { use_data } from '../data/use_data'
import { workflow_state_meta, asset_statuses } from '../theme/status'
import type { ToolbarAction } from '../components/Toolbar'

const total = (workflow: Workflow) => asset_statuses.reduce((sum, status) => sum + workflow.asset_counts[status], 0)
const access_labels: Record<WorkflowAccess, string> = { admin: 'Admin', owner: 'Eigentümer', edit: 'Bearbeiten', run: 'Ausführen', view: 'Ansehen' }
const can_run_workflow = (workflow: Workflow) => ['admin', 'owner', 'edit', 'run'].includes(workflow.access)
const can_edit_workflow = (workflow: Workflow) => ['admin', 'owner', 'edit'].includes(workflow.access)
const can_manage_workflow = (workflow: Workflow) => workflow.access === 'admin' || workflow.access === 'owner'

const name_field: FieldSpec = { key: 'name', label: 'Name', type: 'text' }
const empty_form: WorkflowInput = { name: '', description: '', schedule: null }

export function WorkflowsPage() {
  const navigate = useNavigate()
  const [search_params] = useSearchParams()
  const { data, reload } = use_data(get_workflows, [], { live: ['workflows', 'assets', 'jobs'] })
  use_pausing_notice(data)
  const [selected, set_selected] = useState<string[]>([])
  const [panel_open, set_panel_open] = useState(false)
  const [editing, set_editing] = useState<Workflow | null>(null)
  const [form, set_form] = useState<WorkflowInput>(empty_form)
  const [saving, set_saving] = useState(false)
  const [server_name_error, set_server_name_error] = useState<string | null>(null)
  const [busy_ids, set_busy_ids] = useState<string[]>([])
  const [toggling, set_toggling] = useState(false)
  const [duplicating, set_duplicating] = useState(false)
  const [sharing_workflow, set_sharing_workflow] = useState<Workflow | null>(null)
  const selection_initialized = useRef(false)

  useEffect(() => {
    if (!data || selection_initialized.current) return
    selection_initialized.current = true
    const id = search_params.get('selected')
    if (id && data.some(workflow => workflow.id === id)) set_selected([id])
  }, [data, search_params])

  const selected_workflows = data?.filter(workflow => selected.includes(workflow.id)) ?? []
  const activate = selected_workflows.length === 0 || selected_workflows.some(workflow => !workflow.active)
  const selected_pausing = selected_workflows.some(workflow => workflow.pausing)
  const name_error = data && name_taken(data, form.name, editing?.id ?? null) ? workflow_name_taken_detail : server_name_error

  const open = () => selected[0] && navigate(`/workflows/${selected[0]}/modules`)
  const close_panel = () => set_panel_open(false)
  useEffect(() => {
    if (!panel_open || !editing || !data) return
    const current = data.find(workflow => workflow.id === editing.id)
    if (!current || !can_edit_workflow(current)) {
      set_panel_open(false)
      set_editing(null)
    }
  }, [data, editing, panel_open])
  const open_new = () => {
    set_server_name_error(null)
    set_editing(null)
    set_form({ ...empty_form })
    set_panel_open(true)
  }
  const open_properties = () => {
    const workflow = data?.find(item => item.id === selected[0])
    if (!workflow || !can_edit_workflow(workflow)) return
    set_server_name_error(null)
    set_editing(workflow)
    set_form({ name: workflow.name, description: workflow.description, schedule: workflow.schedule ? { every: workflow.schedule.every, unit: workflow.schedule.unit, start: workflow.schedule.start } : null })
    set_panel_open(true)
  }

  const save = async () => {
    const current_editing = editing && data?.find(workflow => workflow.id === editing.id)
    if (!form.name.trim() || saving || name_error !== null || schedule_error(form.schedule) || (editing && (!current_editing || !can_edit_workflow(current_editing)))) return
    set_saving(true)
    try {
      if (editing) {
        await update_workflow(editing.id, form, { quiet: true })
        close_panel()
        notify('Gespeichert')
        reload()
      } else {
        const created = await create_workflow(form, { quiet: true })
        navigate(`/workflows/${created.id}/modules`, { state: { mode: 'edit' } })
      }
    } catch (error) {
      if (error instanceof ApiError && error.detail === workflow_name_taken_detail) set_server_name_error(error.detail)
      else if (error instanceof ApiError && error.status !== 401) notify(error.message)
    } finally {
      set_saving(false)
    }
  }

  const toggle_active = async (row: Workflow) => {
    if (!can_run_workflow(row)) return
    set_busy_ids(ids => [...ids, row.id])
    try {
      await set_workflows_active([row], !row.active)
      reload()
    } catch {
      return
    } finally {
      set_busy_ids(ids => ids.filter(id => id !== row.id))
    }
  }

  const toggle_selected = async () => {
    if (!selected_workflows.length || !selected_workflows.every(can_run_workflow) || toggling) return
    set_toggling(true)
    try {
      await set_workflows_active(selected_workflows, activate)
    } catch {
      // request already reported
    } finally {
      set_toggling(false)
      reload()
    }
  }

  const duplicate_selected = async () => {
    if (selected.length !== 1 || duplicating) return
    set_duplicating(true)
    try {
      const created = await duplicate_workflow(selected[0])
      notify('Workflow dupliziert')
      set_selected([created.id])
      reload()
    } catch {
      // request already reported
    } finally {
      set_duplicating(false)
    }
  }

  const delete_selected = async () => {
    const workflows = selected.flatMap(id => {
      const workflow = data?.find(item => item.id === id)
      return workflow ? [workflow] : []
    })
    const count = workflows.length
    if (!count || workflows.length !== selected.length || !workflows.every(can_manage_workflow)) return
    const text = count === 1
      ? `Workflow „${workflows[0].name}“ endgültig löschen?\nAlle Assets, Jobs und Logs dieses Workflows werden mitgelöscht.`
      : `${count} Workflows endgültig löschen?\nAlle Assets, Jobs und Logs dieser Workflows werden mitgelöscht.`
    if (!window.confirm(text)) return

    let deleted = 0
    let failed = false
    try {
      for (const workflow of workflows) {
        await delete_workflow(workflow.id)
        deleted += 1
      }
    } catch {
      failed = true
    } finally {
      set_selected([])
      reload()
    }
    if (!failed && deleted > 0) notify(deleted === 1 ? 'Workflow gelöscht' : `${deleted} Workflows gelöscht`)
  }

  const run_column: Column<Workflow> = {
    key: 'run', label: '', width: '48px', sort_value: row => Number(row.active),
    render: row => can_run_workflow(row) ? <button type="button" className="button" title={row.pausing ? 'Wird gestoppt' : row.active ? 'Stoppen' : 'Starten'} aria-label={row.pausing ? 'Wird gestoppt' : row.active ? 'Stoppen' : 'Starten'} disabled={busy_ids.includes(row.id) || row.pausing} onClick={event => { event.stopPropagation(); toggle_active(row) }} onDoubleClick={event => event.stopPropagation()}><Icon name={row.active || row.pausing ? 'stop' : 'play'} size={14} /></button> : null,
  }
  const columns: Column<Workflow>[] = [
    { key: 'name', label: 'Name', render: row => <><strong>{row.name}</strong><IssueMark issues={row.issues} /></> },
    { key: 'owner', label: 'Eigentümer', render: row => row.owner?.email ?? '–', sort_value: row => row.owner?.email ?? '' },
    { key: 'access', label: 'Zugriff', render: row => access_labels[row.access], sort_value: row => access_labels[row.access] },
    { key: 'active', label: 'Status', render: row => <StatusBadge meta={workflow_state_meta(row)} />, sort_value: row => Number(row.active) },
    ...((data?.some(can_run_workflow) ?? false) ? [run_column] : []),
    { key: 'schedule', label: 'Zeitplan', render: row => row.schedule ? <span>{schedule_label(row.schedule)} <span className="dim">· nächster Start {format_datetime(row.schedule.next_run_at)}</span></span> : <span className="dim">–</span>, sort_value: row => row.schedule ? new Date(row.schedule.next_run_at).getTime() : Number.MAX_SAFE_INTEGER },
    { key: 'assets', label: 'Assets', render: row => total(row), sort_value: total, align: 'right' },
    { key: 'distribution', label: 'Verteilung', render: row => <StatusBar counts={row.asset_counts} />, width: '22%' },
    { key: 'description', label: 'Beschreibung', render: row => <span className="dim">{row.description}</span> },
  ]

  const actions: ToolbarAction[] = [
    { label: 'Neu', icon: 'plus', primary: true, on_click: open_new },
    { label: 'Öffnen', icon: 'pencil', on_click: open, disabled: selected.length !== 1 },
    ...(selected_workflows.length === 1 && can_edit_workflow(selected_workflows[0])
      ? [{ label: 'Eigenschaften', icon: 'settings', on_click: open_properties }]
      : []),
    { label: 'Duplizieren', icon: 'copy', on_click: duplicate_selected, disabled: selected.length !== 1 || duplicating },
    ...(selected.length > 0 && selected_workflows.length === selected.length && selected_workflows.every(can_run_workflow)
      ? [{ label: selected_pausing ? 'Wird gestoppt' : activate ? 'Starten' : 'Stoppen', icon: activate && !selected_pausing ? 'play' : 'stop', on_click: toggle_selected, disabled: toggling || selected_pausing }]
      : []),
    ...(selected.length > 0 && selected_workflows.length === selected.length && selected_workflows.every(can_manage_workflow)
      ? [{ label: 'Löschen', icon: 'trash', on_click: delete_selected }]
      : []),
    ...(selected_workflows.length === 1 && can_manage_workflow(selected_workflows[0])
      ? [{ label: 'Teilen', icon: 'users', on_click: () => set_sharing_workflow(selected_workflows[0]) }]
      : []),
  ]

  const share_target = sharing_workflow && data?.find(workflow => workflow.id === sharing_workflow.id)
  return (
    <>
      <PageHeader title="Workflows" actions={actions} />
      <div className="page-content">
        {!data ? <div className="loading">Lade</div> : (
          <DataTable columns={columns} rows={data} row_key={row => row.id} selected={selected}
            on_selection_change={set_selected} on_row_open={row => navigate(`/workflows/${row.id}/modules`)}
            empty_text="Noch keine Workflows – mit „Neu“ anlegen" />
        )}
      </div>
      <SlidePanel open={panel_open} title={editing ? 'Workflow-Eigenschaften' : 'Neuer Workflow'} icon="workflow"
        on_close={close_panel} actions={[
          { label: 'Abbrechen', icon: 'close', on_click: close_panel },
          { label: 'Speichern', icon: 'save', primary: true, on_click: save, disabled: !form.name.trim() || saving || name_error !== null || schedule_error(form.schedule) !== null },
        ]}>
        <FormField spec={name_field} value={form.name} disabled={saving} error={name_error} on_change={value => { set_server_name_error(null); set_form(current => ({ ...current, name: String(value) })) }} />
        <div className="form-field"><label htmlFor="field-description" className="form-field-label">Beschreibung</label><div className="form-field-control"><AutoTextarea id="field-description" value={form.description} disabled={saving} on_change={value => set_form(current => ({ ...current, description: value }))} /></div></div>
        <ScheduleFields value={form.schedule} on_change={schedule => set_form(current => ({ ...current, schedule }))} disabled={saving} next_run_at={editing?.schedule?.next_run_at ?? null} />
      </SlidePanel>
      {share_target && can_manage_workflow(share_target) && <ShareDialog workflow={share_target} on_close={() => set_sharing_workflow(null)} on_owner_changed={reload} />}
    </>
  )
}
