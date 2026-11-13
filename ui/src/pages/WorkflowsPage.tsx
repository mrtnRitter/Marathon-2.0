import { useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { DataTable, type Column } from '../components/DataTable'
import { FormField } from '../components/FormField'
import { AutoTextarea } from '../components/AutoTextarea'
import { Icon } from '../components/Icon'
import { PageHeader } from '../components/PageHeader'
import { SlidePanel } from '../components/SlidePanel'
import { StatusBadge } from '../components/StatusBadge'
import { StatusBar } from '../components/StatusBar'
import { ScheduleFields } from '../features/workflows/ScheduleFields'
import { notify } from '../components/Toast'
import { ApiError, create_workflow, delete_workflow, duplicate_workflow, get_workflows, update_workflow, workflow_name_taken_detail } from '../data/api'
import type { FieldSpec, Workflow, WorkflowInput } from '../data/types'
import { schedule_error, schedule_label } from '../lib/schedule'
import { format_datetime } from '../lib/format'
import { name_taken, set_workflows_active } from '../lib/workflows'
import { use_data } from '../data/use_data'
import { active_meta, asset_statuses } from '../theme/status'

const total = (workflow: Workflow) => asset_statuses.reduce((sum, status) => sum + workflow.asset_counts[status], 0)

const name_field: FieldSpec = { key: 'name', label: 'Name', type: 'text' }
const empty_form: WorkflowInput = { name: '', description: '', schedule: null }

export function WorkflowsPage() {
  const navigate = useNavigate()
  const [search_params] = useSearchParams()
  const { data, reload } = use_data(get_workflows)
  const [selected, set_selected] = useState<string[]>([])
  const [panel_open, set_panel_open] = useState(false)
  const [editing, set_editing] = useState<Workflow | null>(null)
  const [form, set_form] = useState<WorkflowInput>(empty_form)
  const [saving, set_saving] = useState(false)
  const [server_name_error, set_server_name_error] = useState<string | null>(null)
  const [busy_ids, set_busy_ids] = useState<string[]>([])
  const [toggling, set_toggling] = useState(false)
  const [duplicating, set_duplicating] = useState(false)
  const selection_initialized = useRef(false)

  useEffect(() => {
    if (!data || selection_initialized.current) return
    selection_initialized.current = true
    const id = search_params.get('selected')
    if (id && data.some(workflow => workflow.id === id)) set_selected([id])
  }, [data, search_params])

  const selected_workflows = data?.filter(workflow => selected.includes(workflow.id)) ?? []
  const activate = selected_workflows.length === 0 || selected_workflows.some(workflow => !workflow.active)
  const name_error = data && name_taken(data, form.name, editing?.id ?? null) ? workflow_name_taken_detail : server_name_error

  const open = () => selected[0] && navigate(`/workflows/${selected[0]}/modules`)
  const close_panel = () => set_panel_open(false)
  const open_new = () => {
    set_server_name_error(null)
    set_editing(null)
    set_form({ ...empty_form })
    set_panel_open(true)
  }
  const open_properties = () => {
    const workflow = data?.find(item => item.id === selected[0])
    if (!workflow) return
    set_server_name_error(null)
    set_editing(workflow)
    set_form({ name: workflow.name, description: workflow.description, schedule: workflow.schedule ? { every: workflow.schedule.every, unit: workflow.schedule.unit, start: workflow.schedule.start } : null })
    set_panel_open(true)
  }

  const save = async () => {
    if (!form.name.trim() || saving || name_error !== null || schedule_error(form.schedule)) return
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
      else if (error instanceof ApiError) notify(error.message)
    } finally {
      set_saving(false)
    }
  }

  const toggle_active = async (row: Workflow) => {
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
    if (!selected_workflows.length || toggling) return
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
    if (!count) return
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

  const columns: Column<Workflow>[] = [
    { key: 'name', label: 'Name', render: row => <strong>{row.name}</strong> },
    { key: 'active', label: 'Status', render: row => <StatusBadge meta={active_meta(row.active)} />, sort_value: row => Number(row.active) },
    { key: 'run', label: '', width: '48px', sort_value: row => Number(row.active), render: row => <button type="button" className="button" title={row.active ? 'Pausieren' : 'Starten'} aria-label={row.active ? 'Pausieren' : 'Starten'} disabled={busy_ids.includes(row.id)} onClick={event => { event.stopPropagation(); toggle_active(row) }} onDoubleClick={event => event.stopPropagation()}><Icon name={row.active ? 'pause' : 'play'} size={14} /></button> },
    { key: 'schedule', label: 'Zeitplan', render: row => row.schedule ? <span>{schedule_label(row.schedule)} <span className="dim">· nächster Start {format_datetime(row.schedule.next_run_at)}</span></span> : <span className="dim">–</span>, sort_value: row => row.schedule ? new Date(row.schedule.next_run_at).getTime() : Number.MAX_SAFE_INTEGER },
    { key: 'assets', label: 'Assets', render: row => total(row), sort_value: total, align: 'right' },
    { key: 'distribution', label: 'Verteilung', render: row => <StatusBar counts={row.asset_counts} />, width: '22%' },
    { key: 'description', label: 'Beschreibung', render: row => <span className="dim">{row.description}</span> },
  ]

  return (
    <>
      <PageHeader title="Workflows" actions={[
        { label: 'Neu', icon: 'plus', primary: true, on_click: open_new },
        { label: 'Öffnen', icon: 'pencil', on_click: open, disabled: selected.length !== 1 },
        { label: 'Eigenschaften', icon: 'settings', on_click: open_properties, disabled: selected.length !== 1 },
        { label: 'Duplizieren', icon: 'copy', on_click: duplicate_selected, disabled: selected.length !== 1 || duplicating },
        { label: activate ? 'Aktivieren' : 'Pausieren', icon: activate ? 'play' : 'pause', on_click: toggle_selected, disabled: !selected.length || toggling },
        { label: 'Löschen', icon: 'trash', on_click: delete_selected, disabled: !selected.length },
      ]} />
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
    </>
  )
}
