import { useState } from 'react'
import { DataTable, type Column } from '../components/DataTable'
import { FormField, FormSection } from '../components/FormField'
import { PageHeader } from '../components/PageHeader'
import { SlidePanel } from '../components/SlidePanel'
import { StatusBadge } from '../components/StatusBadge'
import { StatusTabs } from '../components/StatusTabs'
import { notify, not_available } from '../components/Toast'
import { approve_worker, get_module_types, get_workers, lock_worker, update_worker } from '../data/api'
import type { FieldSpec, ModuleType, Worker, WorkerInput, WorkerPriority, WorkerStatus } from '../data/types'
import { use_data } from '../data/use_data'
import { format_datetime, format_relative } from '../lib/format'
import { worker_status_meta } from '../theme/status'

const columns: Column<Worker>[] = [
  { key: 'name', label: 'Name', render: row => <strong>{row.name}</strong> },
  { key: 'status', label: 'Status', render: row => <StatusBadge meta={worker_status_meta[row.status]} /> },
  { key: 'capabilities', label: 'Fähigkeiten', render: row => row.capabilities.length ? row.capabilities.map(item => <span key={item} className="chip">{item}</span>) : <span className="dim">–</span> },
  { key: 'max_parallel', label: 'Parallel', align: 'right' },
  { key: 'running_jobs', label: 'Laufende Jobs', render: row => `${row.running_jobs} / ${row.max_parallel}`, sort_value: row => row.running_jobs, align: 'right' },
  { key: 'client_version', label: 'Client-Version' },
  { key: 'last_seen', label: 'Zuletzt gesehen', render: row => <span title={format_datetime(row.last_seen)}>{format_relative(row.last_seen)}</span>, sort_value: row => row.last_seen },
]

const name_field: FieldSpec = { key: 'name', label: 'Name', type: 'text' }
const priority_choices: { value: WorkerPriority; label: string }[] = [
  { value: 'preferred', label: 'Bevorzugt' },
  { value: 'normal', label: 'Normal' },
  { value: 'last', label: 'Letzte Wahl' },
  { value: 'off', label: 'Aus' },
]
const status_tiles: WorkerStatus[] = ['ready', 'busy', 'new', 'updating', 'offline', 'locked']

export function WorkersPage() {
  const { data, reload } = use_data(() => Promise.all([get_workers(), get_module_types()]), [], { live: ['workers', 'jobs', 'module_types'] })
  const [selected, set_selected] = useState<string[]>([])
  const [editing_worker, set_editing_worker] = useState<Worker | null>(null)
  const [form, set_form] = useState<WorkerInput | null>(null)
  const [saving, set_saving] = useState(false)
  const [acting, set_acting] = useState(false)
  const workers = data?.[0]
  const module_types = data?.[1] ?? []

  const edit = (worker: Worker) => {
    set_editing_worker(worker)
    set_form({
      name: worker.name,
      max_parallel: worker.max_parallel,
      priorities: Object.fromEntries(worker.capabilities.map(id => [id, worker.priorities[id] ?? 'normal'])),
    })
  }

  const save = async () => {
    if (!editing_worker || !form || saving || !form.name.trim() || form.max_parallel < 1 || form.max_parallel > editing_worker.client_slots || form.max_parallel > 64) return
    set_saving(true)
    try {
      await update_worker(editing_worker.id, form)
      notify('Gespeichert')
      set_editing_worker(null)
      set_form(null)
      reload()
    } catch {
      // The request already displays the error.
    } finally {
      set_saving(false)
    }
  }

  const change_worker_access = async (approve: boolean) => {
    if (!selected.length || acting) return
    set_acting(true)
    let completed = 0
    for (const id of selected) {
      try {
        if (approve) await approve_worker(id)
        else await lock_worker(id)
        completed += 1
      } catch {
        // Continue so every selected worker is attempted.
      }
    }
    reload()
    if (completed === 1) notify(approve ? 'Worker freigegeben' : 'Worker gesperrt')
    else if (completed > 1) notify(`${completed} Worker ${approve ? 'freigegeben' : 'gesperrt'}`)
    set_acting(false)
  }

  const close_panel = () => {
    set_editing_worker(null)
    set_form(null)
  }

  const priority_field = (module: Pick<ModuleType, 'id' | 'name'>, priority: WorkerPriority): FieldSpec => ({
    key: `priority-${module.id}`,
    label: `Vorrang: ${module.name || module.id}`,
    type: 'select',
    options: priority_choices.map(choice => choice.label),
    default: priority_choices.find(choice => choice.value === priority)?.label ?? 'Normal',
  })

  const none = selected.length === 0
  return (
    <>
      <PageHeader title="Worker" actions={[
        { label: 'Bearbeiten', icon: 'pencil', disabled: selected.length !== 1 || !workers, on_click: () => { const worker = workers?.find(item => item.id === selected[0]); if (worker) edit(worker) } },
        { label: 'Freigeben', icon: 'check', disabled: none || acting, on_click: () => change_worker_access(true) },
        { label: 'Sperren', icon: 'lock', disabled: none || acting, on_click: () => change_worker_access(false) },
        { label: 'Version ausrollen', icon: 'rocket', disabled: none, on_click: not_available },
      ]} />
      <div className="page-content">
        {!workers ? <div className="loading">Lade</div> : (
          <>
            <StatusTabs items={status_tiles.map(status => ({ id: status, ...worker_status_meta[status], count: workers.filter(worker => worker.status === status).length }))} />
            <DataTable columns={columns} rows={workers} row_key={row => row.id} selected={selected} on_selection_change={set_selected} on_row_open={edit} />
          </>
        )}
      </div>
      <SlidePanel open={editing_worker !== null && form !== null} title={editing_worker?.name ?? ''} icon="server" on_close={close_panel}
        actions={[
          { label: 'Abbrechen', icon: 'close', on_click: close_panel, disabled: saving },
          { label: 'Speichern', icon: 'save', primary: true, on_click: save, disabled: !form?.name.trim() || !editing_worker || !form || form.max_parallel < 1 || form.max_parallel > (editing_worker?.client_slots ?? 0) || form.max_parallel > 64 || saving },
        ]}>
        {editing_worker && form && (
          <FormSection title="Allgemein">
            <FormField spec={name_field} value={form.name} disabled={saving} on_change={value => set_form(current => current ? { ...current, name: String(value) } : current)} />
            <FormField spec={{ key: 'max_parallel', label: 'Parallele Jobs', type: 'number', min: 1, max: Math.min(64, editing_worker.client_slots), help: `Höchstens so viele, wie der Worker selbst anbietet (${editing_worker.client_slots})` }} value={form.max_parallel} disabled={saving} on_change={value => set_form(current => current ? { ...current, max_parallel: Number(value) } : current)} />
          </FormSection>
        )}
        {editing_worker && form && (
          <FormSection title="Vorrang">
            {editing_worker.capabilities.map(id => {
              const module = module_types.find(item => item.id === id)
              const priority = form.priorities[id] ?? 'normal'
              const spec = priority_field(module ?? { id, name: id }, priority)
              const label = priority_choices.find(choice => choice.value === priority)?.label ?? 'Normal'
              return <FormField key={id} spec={spec} value={label} disabled={saving} on_change={value => set_form(current => current ? { ...current, priorities: { ...current.priorities, [id]: priority_choices.find(choice => choice.label === String(value))?.value ?? 'normal' } } : current)} />
            })}
          </FormSection>
        )}
      </SlidePanel>
    </>
  )
}
