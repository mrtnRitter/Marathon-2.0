import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { DataTable, type Column } from '../components/DataTable'
import { FormField } from '../components/FormField'
import { PageHeader } from '../components/PageHeader'
import { SlidePanel } from '../components/SlidePanel'
import { StatusBadge } from '../components/StatusBadge'
import { StatusBar } from '../components/StatusBar'
import { notify } from '../components/Toast'
import { create_workflow, delete_workflow, get_workflows, update_workflow } from '../data/api'
import type { FieldSpec, Workflow, WorkflowInput } from '../data/types'
import { use_data } from '../data/use_data'
import { active_meta, asset_statuses } from '../theme/status'

const total = (workflow: Workflow) => asset_statuses.reduce((sum, status) => sum + workflow.asset_counts[status], 0)

const name_field: FieldSpec = { key: 'name', label: 'Name', type: 'text' }
const description_field: FieldSpec = { key: 'description', label: 'Beschreibung', type: 'text' }
const priority_field: FieldSpec = { key: 'priority', label: 'Priorität', type: 'number', help: 'Höhere Zahl = wird zuerst verarbeitet' }

const columns: Column<Workflow>[] = [
  { key: 'name', label: 'Name', render: row => <strong>{row.name}</strong> },
  { key: 'active', label: 'Status', render: row => <StatusBadge meta={active_meta(row.active)} />, sort_value: row => Number(row.active) },
  { key: 'version', label: 'Version', render: row => `v${row.version}`, align: 'right' },
  { key: 'priority', label: 'Priorität', align: 'right' },
  { key: 'assets', label: 'Assets', render: row => total(row), sort_value: total, align: 'right' },
  { key: 'distribution', label: 'Verteilung', render: row => <StatusBar counts={row.asset_counts} />, width: '22%' },
  { key: 'description', label: 'Beschreibung', render: row => <span className="dim">{row.description}</span> },
]

const empty_form: WorkflowInput = { name: '', description: '', priority: 0 }

export function WorkflowsPage() {
  const navigate = useNavigate()
  const { data, reload } = use_data(get_workflows)
  const [selected, set_selected] = useState<string[]>([])
  const [panel_open, set_panel_open] = useState(false)
  const [editing, set_editing] = useState<Workflow | null>(null)
  const [form, set_form] = useState<WorkflowInput>(empty_form)
  const [saving, set_saving] = useState(false)

  const open = () => selected[0] && navigate(`/workflows/${selected[0]}/modules`)
  const close_panel = () => set_panel_open(false)
  const open_new = () => {
    set_editing(null)
    set_form({ ...empty_form })
    set_panel_open(true)
  }
  const open_properties = () => {
    const workflow = data?.find(item => item.id === selected[0])
    if (!workflow) return
    set_editing(workflow)
    set_form({ name: workflow.name, description: workflow.description, priority: workflow.priority })
    set_panel_open(true)
  }

  const save = async () => {
    if (!form.name.trim() || saving) return
    set_saving(true)
    try {
      if (editing) {
        await update_workflow(editing.id, form)
        close_panel()
        notify('Gespeichert')
        reload()
      } else {
        const created = await create_workflow(form)
        navigate(`/workflows/${created.id}/modules`)
      }
    } catch {
      return
    } finally {
      set_saving(false)
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

  return (
    <>
      <PageHeader title="Workflows" actions={[
        { label: 'Neu', icon: 'plus', primary: true, on_click: open_new },
        { label: 'Öffnen', icon: 'pencil', on_click: open, disabled: selected.length !== 1 },
        { label: 'Eigenschaften', icon: 'settings', on_click: open_properties, disabled: selected.length !== 1 },
        { label: 'Duplizieren', icon: 'copy', disabled: selected.length !== 1 },
        { label: 'Pausieren/Aktivieren', icon: 'pause', disabled: !selected.length },
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
          { label: 'Speichern', icon: 'save', primary: true, on_click: save, disabled: !form.name.trim() || saving },
        ]}>
        <FormField spec={name_field} value={form.name} disabled={saving} on_change={value => set_form(current => ({ ...current, name: String(value) }))} />
        <FormField spec={description_field} value={form.description} disabled={saving} on_change={value => set_form(current => ({ ...current, description: String(value) }))} />
        <FormField spec={priority_field} value={form.priority} disabled={saving} on_change={value => set_form(current => ({ ...current, priority: Number(value) }))} />
      </SlidePanel>
    </>
  )
}
