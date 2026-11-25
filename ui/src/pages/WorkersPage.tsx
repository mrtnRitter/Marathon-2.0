import { useMemo, useState } from 'react'
import { DataTable, type Column } from '../components/DataTable'
import { FormField, FormSection } from '../components/FormField'
import { Icon } from '../components/Icon'
import { PageHeader } from '../components/PageHeader'
import { SkillListField } from '../components/SkillListField'
import { SlidePanel } from '../components/SlidePanel'
import { StatusBadge } from '../components/StatusBadge'
import { StatusTabs } from '../components/StatusTabs'
import { notify, not_available } from '../components/Toast'
import { get_module_types, get_workers, start_worker, stop_worker, update_worker } from '../data/api'
import { use_auth_user } from '../data/auth'
import type { FieldSpec, Worker, WorkerInput, WorkerStatus } from '../data/types'
import { use_data } from '../data/use_data'
import { use_server_connected } from '../data/use_connection'
import { format_datetime, format_relative } from '../lib/format'
import { shown_skills } from '../lib/skills'
import { is_online, online_meta, shown_worker_status, worker_status_meta } from '../theme/status'

const name_field: FieldSpec = { key: 'name', label: 'Name', type: 'text' }
const status_tiles: WorkerStatus[] = ['ready', 'stopping', 'stopped']
const skills_help = 'Weiter oben = höhere Priorität. Nicht in der Liste = übernimmt diesen Modultyp nicht.'

export function WorkersPage() {
  const connected = use_server_connected()
  const is_admin = use_auth_user()?.role === 'admin'
  const { data, reload } = use_data(() => Promise.all([get_workers(), get_module_types()]), [], { live: ['workers', 'jobs', 'module_types'] })
  const [selected, set_selected] = useState<string[]>([])
  const [editing_worker, set_editing_worker] = useState<Worker | null>(null)
  const [form, set_form] = useState<WorkerInput | null>(null)
  const [saving, set_saving] = useState(false)
  const [acting, set_acting] = useState(false)
  const [busy_ids, set_busy_ids] = useState<string[]>([])
  const [panel_read_only, set_panel_read_only] = useState(false)
  const workers = data?.[0]
  const module_types = data?.[1] ?? []
  const selected_workers = workers?.filter(worker => selected.includes(worker.id)) ?? []
  const selected_stopping = selected_workers.some(worker => worker.status === 'stopping')
  const start_selected = selected_workers.length > 0 && selected_workers.every(worker => worker.status === 'stopped')
  const read_only_panel = panel_read_only || !is_admin

  const edit = (worker: Worker, read_only = false) => {
    set_editing_worker(worker)
    set_panel_read_only(read_only || !is_admin)
    set_form({ name: worker.name, skills: shown_skills(worker.skills, worker.capabilities) })
  }

  const save = async () => {
    if (!is_admin || panel_read_only || !editing_worker || !form || saving || !form.name.trim()) return
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

  const toggle_worker = async (worker: Worker) => {
    if (!is_admin || worker.status === 'stopping' || busy_ids.includes(worker.id)) return
    const start = worker.status === 'stopped'
    set_busy_ids(ids => [...ids, worker.id])
    try {
      if (start) await start_worker(worker.id)
      else await stop_worker(worker.id)
      notify(start ? 'Worker gestartet' : 'Worker gestoppt')
    } catch {
      // The request already displays the error.
    } finally {
      set_busy_ids(ids => ids.filter(id => id !== worker.id))
      reload()
    }
  }

  const toggle_selected = async () => {
    if (!is_admin || !selected_workers.length || acting || selected_stopping) return
    set_acting(true)
    let completed = 0
    try {
      for (const worker of selected_workers) {
        try {
          if (start_selected) await start_worker(worker.id)
          else await stop_worker(worker.id)
          completed += 1
        } catch {
          // Continue so every selected worker is attempted.
        }
      }
    } finally {
      set_acting(false)
      reload()
    }
    if (completed === 1) notify(start_selected ? 'Worker gestartet' : 'Worker gestoppt')
    else if (completed > 1) notify(`${completed} Worker ${start_selected ? 'gestartet' : 'gestoppt'}`)
  }

  const close_panel = () => {
    set_editing_worker(null)
    set_form(null)
    set_panel_read_only(false)
  }

  const columns = useMemo<Column<Worker>[]>(() => [
    ...(is_admin ? [{ key: 'run', label: '', width: '48px', sort_value: (row: Worker) => Number(row.status === 'ready'), render: (row: Worker) => <button type="button" className="button" title={row.status === 'stopping' ? 'Wird gestoppt' : row.status === 'stopped' ? 'Starten' : 'Stoppen'} aria-label={row.status === 'stopping' ? 'Wird gestoppt' : row.status === 'stopped' ? 'Starten' : 'Stoppen'} disabled={row.status === 'stopping' || busy_ids.includes(row.id)} onClick={event => { event.stopPropagation(); void toggle_worker(row) }} onDoubleClick={event => event.stopPropagation()}><Icon name={row.status === 'stopped' ? 'play' : 'stop'} size={14} /></button> }] : []),
    { key: 'name', label: 'Name', render: row => <strong>{row.name}</strong> },
    { key: 'status', label: 'Status', render: row => <StatusBadge meta={worker_status_meta[shown_worker_status(row, connected)]} /> },
    { key: 'online', label: 'Verbindung', render: row => <StatusBadge meta={online_meta(is_online(row, connected))} />, sort_value: row => Number(is_online(row, connected)) },
    { key: 'skills', label: 'Fähigkeiten', render: row => { const skills = shown_skills(row.skills, row.capabilities); return skills.length ? skills.map(id => <span key={id} className="chip">{module_types.find(module => module.id === id)?.name || id}</span>) : <span className="dim">–</span> }, sort_value: row => shown_skills(row.skills, row.capabilities).map(id => module_types.find(module => module.id === id)?.name || id).join(', ') },
    { key: 'running_jobs', label: 'Jobs', render: row => row.running_jobs, sort_value: row => row.running_jobs, align: 'right' },
    { key: 'client_version', label: 'Client-Version' },
    { key: 'last_seen', label: 'Zuletzt gesehen', render: row => <span title={format_datetime(row.last_seen)}>{format_relative(row.last_seen)}</span>, sort_value: row => row.last_seen },
  ], [busy_ids, connected, is_admin, module_types])

  const status_items = [
    ...status_tiles.map(status => ({ id: status, ...worker_status_meta[status], count: workers?.filter(worker => worker.status === status).length ?? 0 })),
    { id: 'offline', ...online_meta(false), count: workers?.filter(worker => !is_online(worker, connected)).length ?? 0 },
  ]
  const none = selected_workers.length === 0

  return (
    <>
      <PageHeader title="Worker" actions={is_admin ? [
        { label: 'Bearbeiten', icon: 'pencil', disabled: selected_workers.length !== 1 || !workers, on_click: () => { const worker = selected_workers[0]; if (worker) edit(worker) } },
        { label: selected_stopping ? 'Wird gestoppt' : start_selected ? 'Starten' : 'Stoppen', icon: start_selected && !selected_stopping ? 'play' : 'stop', disabled: none || acting || selected_stopping, on_click: toggle_selected },
        { label: 'Version ausrollen', icon: 'rocket', disabled: none, on_click: not_available },
      ] : []} />
      <div className="page-content">
        {!workers ? <div className="loading">Lade</div> : (
          <>
            <StatusTabs items={status_items} />
            <DataTable columns={columns} rows={workers} row_key={row => row.id} selected={selected} on_selection_change={set_selected} on_row_open={worker => edit(worker, !is_admin)} />
          </>
        )}
      </div>
      <SlidePanel open={editing_worker !== null && form !== null} title={editing_worker?.name ?? ''} icon="server" on_close={close_panel}
        actions={[
          { label: read_only_panel ? 'Schließen' : 'Abbrechen', icon: 'close', on_click: close_panel, disabled: saving },
          ...(!read_only_panel && is_admin ? [{ label: 'Speichern', icon: 'save', primary: true, on_click: save, disabled: !form?.name.trim() || !editing_worker || !form || saving }] : []),
        ]}>
        {editing_worker && form && (
          <FormSection title="Allgemein">
            {read_only_panel
              ? <div className="form-field"><div className="form-field-label">Name</div><div>{editing_worker.name}</div></div>
              : <FormField spec={name_field} value={form.name} disabled={saving} on_change={value => set_form(current => current ? { ...current, name: String(value) } : current)} />}
          </FormSection>
        )}
        {editing_worker && form && (
          <FormSection title="Fähigkeiten">
            {read_only_panel ? (
              <div className="form-field">
                <div className="form-field-label">Fähigkeiten</div>
                <div>{form.skills.length ? form.skills.map(id => <span key={id} className="chip">{module_types.find(module => module.id === id)?.name || id}</span>) : <span className="dim">–</span>}</div>
              </div>
            ) : (
              <>
                <div className="form-field-help">{skills_help}</div>
                <SkillListField value={form.skills} capabilities={editing_worker.capabilities} modules={module_types} disabled={saving} on_change={skills => set_form(current => current ? { ...current, skills } : current)} />
              </>
            )}
          </FormSection>
        )}
      </SlidePanel>
    </>
  )
}
