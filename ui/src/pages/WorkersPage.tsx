import { useMemo, useState } from 'react'
import { AvailabilityEditor } from '../components/AvailabilityEditor'
import { DataTable, type Column } from '../components/DataTable'
import { FormField, FormSection } from '../components/FormField'
import { Icon } from '../components/Icon'
import { PageHeader } from '../components/PageHeader'
import { SkillListField } from '../components/SkillListField'
import { SlidePanel } from '../components/SlidePanel'
import { StatusBadge } from '../components/StatusBadge'
import { StatusTabs } from '../components/StatusTabs'
import { notify, not_available } from '../components/Toast'
import { approve_worker, client_download_url, delete_worker, get_client_info, get_module_types, get_workers, start_worker, stop_drain_worker, stop_now_worker, update_worker, update_worker_availability } from '../data/api'
import { use_auth_user } from '../data/auth'
import type { AvailabilityConfig, FieldSpec, Worker, WorkerInput, WorkerStatus } from '../data/types'
import { use_data } from '../data/use_data'
import { use_server_connected } from '../data/use_connection'
import { format_datetime, format_relative } from '../lib/format'
import { availability_text, can_delete, can_start, can_stop_drain, can_stop_now } from '../lib/worker_actions'
import { shown_skills } from '../lib/skills'
import { is_online, online_meta, shown_worker_status, worker_approval_meta, worker_status_meta } from '../theme/status'

const name_field: FieldSpec = { key: 'name', label: 'Name', type: 'text' }
const status_tiles: WorkerStatus[] = ['ready', 'draining', 'stopping', 'stopped']
const skills_help = 'Weiter oben = höhere Priorität. Nicht in der Liste = übernimmt diesen Modultyp nicht.'
type WorkerAction = 'start' | 'stop_drain' | 'stop_now' | 'approve'

const action_allowed: Record<WorkerAction, (worker: Worker) => boolean> = {
  start: can_start,
  stop_drain: can_stop_drain,
  stop_now: can_stop_now,
  approve: worker => !worker.approved,
}

async function perform_action(action: WorkerAction, id: string): Promise<Worker> {
  if (action === 'start') return start_worker(id)
  if (action === 'stop_drain') return stop_drain_worker(id)
  if (action === 'stop_now') return stop_now_worker(id)
  return approve_worker(id)
}

function action_message(action: WorkerAction, count: number): string {
  if (action === 'start') return count === 1 ? 'Worker gestartet' : `${count} Worker gestartet`
  if (action === 'stop_drain') return count === 1 ? 'Worker wird gestoppt' : `${count} Worker werden gestoppt`
  if (action === 'stop_now') return count === 1 ? 'Worker wird sofort gestoppt' : `${count} Worker werden sofort gestoppt`
  return count === 1 ? 'Worker zugelassen' : `${count} Worker zugelassen`
}

export function WorkersPage() {
  const connected = use_server_connected()
  const is_admin = use_auth_user()?.role === 'admin'
  const { data, reload } = use_data(() => Promise.all([get_workers(), get_module_types()]), [], { live: ['workers', 'jobs', 'module_types'] })
  const { data: client_info, error: client_info_error } = use_data(get_client_info)
  const [selected, set_selected] = useState<string[]>([])
  const [editing_worker, set_editing_worker] = useState<Worker | null>(null)
  const [form, set_form] = useState<WorkerInput | null>(null)
  const [availability_config, set_availability_config] = useState<AvailabilityConfig | null>(null)
  const [saving, set_saving] = useState(false)
  const [acting, set_acting] = useState(false)
  const [busy_ids, set_busy_ids] = useState<string[]>([])
  const [panel_read_only, set_panel_read_only] = useState(false)
  const [removal_workers, set_removal_workers] = useState<Worker[] | null>(null)
  const [removing, set_removing] = useState(false)
  const workers = data?.[0]
  const module_types = data?.[1] ?? []
  const selected_workers = workers?.filter(worker => selected.includes(worker.id)) ?? []
  const read_only_panel = panel_read_only || !is_admin

  const edit = (worker: Worker, read_only = false) => {
    set_editing_worker(worker)
    set_panel_read_only(read_only || !is_admin)
    set_form({ name: worker.name, skills: shown_skills(worker.skills, worker.capabilities) })
    set_availability_config(worker.availability_config)
  }

  const save = async () => {
    if (!is_admin || panel_read_only || !editing_worker || !form || !availability_config || saving || !form.name.trim()) return
    set_saving(true)
    try {
      await update_worker(editing_worker.id, form)
      if (JSON.stringify(availability_config) !== JSON.stringify(editing_worker.availability_config)) {
        await update_worker_availability(editing_worker.id, availability_config)
      }
      notify('Gespeichert')
      set_editing_worker(null)
      set_form(null)
      set_availability_config(null)
      reload()
    } catch {
      // The request already displays the error.
    } finally {
      set_saving(false)
    }
  }

  const run_worker_action = async (worker: Worker, action: WorkerAction) => {
    if (!is_admin || !action_allowed[action](worker) || busy_ids.includes(worker.id)) return
    set_busy_ids(ids => [...ids, worker.id])
    try {
      await perform_action(action, worker.id)
      notify(action_message(action, 1))
    } catch {
      // The request already displays the error.
    } finally {
      set_busy_ids(ids => ids.filter(id => id !== worker.id))
      reload()
    }
  }

  const run_selected_action = async (action: WorkerAction) => {
    if (!is_admin || acting) return
    const eligible = selected_workers.filter(worker => action_allowed[action](worker) && !busy_ids.includes(worker.id))
    if (!eligible.length) return
    set_acting(true)
    let completed = 0
    try {
      for (const worker of eligible) {
        try {
          await perform_action(action, worker.id)
          completed += 1
        } catch {
          // Continue so every eligible worker is attempted.
        }
      }
    } finally {
      set_acting(false)
      reload()
    }
    if (completed) notify(action_message(action, completed))
  }

  const open_removal = () => {
    const eligible = selected_workers.filter(can_delete)
    if (eligible.length) set_removal_workers(eligible)
  }

  const close_removal = () => {
    if (!removing) set_removal_workers(null)
  }

  const confirm_removal = async () => {
    if (!is_admin || !removal_workers || removing) return
    set_removing(true)
    let completed = 0
    try {
      for (const selected_worker of removal_workers) {
        const current_worker = workers?.find(worker => worker.id === selected_worker.id)
        if (!current_worker || !can_delete(current_worker)) continue
        try {
          await delete_worker(current_worker.id)
          completed += 1
        } catch {
          // Continue so every currently eligible worker is attempted.
        }
      }
    } finally {
      set_removing(false)
      set_removal_workers(null)
      reload()
    }
    if (completed === 1) notify('Worker entfernt')
    else if (completed > 1) notify(`${completed} Worker entfernt`)
  }

  const close_panel = () => {
    set_editing_worker(null)
    set_form(null)
    set_availability_config(null)
    set_panel_read_only(false)
  }

  const columns = useMemo<Column<Worker>[]>(() => [
    ...(is_admin ? [{
      key: 'run', label: '', width: '156px',
      render: (row: Worker) => <div className="toolbar">
        <button type="button" className="button" title="Starten" aria-label="Starten" disabled={!can_start(row) || busy_ids.includes(row.id)} onClick={event => { event.stopPropagation(); void run_worker_action(row, 'start') }} onDoubleClick={event => event.stopPropagation()}><Icon name="play" size={14} /></button>
        <button type="button" className="button" title="Stoppen" aria-label="Stoppen" disabled={!can_stop_drain(row) || busy_ids.includes(row.id)} onClick={event => { event.stopPropagation(); void run_worker_action(row, 'stop_drain') }} onDoubleClick={event => event.stopPropagation()}><Icon name="stop" size={14} /></button>
        <button type="button" className="button" title="Sofort-Aus" aria-label="Sofort-Aus" disabled={!can_stop_now(row) || busy_ids.includes(row.id)} onClick={event => { event.stopPropagation(); void run_worker_action(row, 'stop_now') }} onDoubleClick={event => event.stopPropagation()}><Icon name="stop-red" size={14} /></button>
      </div>,
    }] : []),
    { key: 'name', label: 'Name', render: row => <strong>{row.name}</strong> },
    { key: 'status', label: 'Status', render: row => <>{<StatusBadge meta={worker_status_meta[shown_worker_status(row, connected)]} />}{!row.approved && <StatusBadge meta={worker_approval_meta} />}</> },
    { key: 'online', label: 'Verbindung', render: row => <StatusBadge meta={online_meta(is_online(row, connected))} />, sort_value: row => Number(is_online(row, connected)) },
    { key: 'available', label: 'Verfügbar', render: availability_text, sort_value: availability_text },
    { key: 'skills', label: 'Fähigkeiten', render: row => { const skills = shown_skills(row.skills, row.capabilities); return skills.length ? skills.map(id => <span key={id} className="chip">{module_types.find(module => module.id === id)?.name || id}</span>) : <span className="dim">–</span> }, sort_value: row => shown_skills(row.skills, row.capabilities).map(id => module_types.find(module => module.id === id)?.name || id).join(', ') },
    { key: 'running_jobs', label: 'Jobs', render: row => row.running_jobs, sort_value: row => row.running_jobs, align: 'right' },
    { key: 'client_version', label: 'Client-Version' },
    { key: 'last_seen', label: 'Zuletzt gesehen', render: row => <span title={format_datetime(row.last_seen)}>{format_relative(row.last_seen)}</span>, sort_value: row => row.last_seen },
  ], [busy_ids, connected, is_admin, module_types])

  const status_items = [
    ...status_tiles.map(status => ({ id: status, ...worker_status_meta[status], count: workers?.filter(worker => worker.status === status).length ?? 0 })),
    { id: 'offline', ...online_meta(false), count: workers?.filter(worker => !is_online(worker, connected)).length ?? 0 },
    { id: 'pending-approval', ...worker_approval_meta, count: workers?.filter(worker => !worker.approved).length ?? 0 },
  ]
  const none = selected_workers.length === 0
  const client_unavailable_title = client_info?.detail || (client_info_error ? 'Clientinformationen nicht verfügbar' : 'Clientinformationen werden geladen')

  return (
    <>
      <PageHeader title="Worker" actions={is_admin ? [
        { label: 'Starten', icon: 'play', disabled: acting || !selected_workers.some(can_start), on_click: () => { void run_selected_action('start') } },
        { label: 'Stoppen', icon: 'stop', disabled: acting || !selected_workers.some(can_stop_drain), on_click: () => { void run_selected_action('stop_drain') } },
        { label: 'Sofort-Aus', icon: 'stop-red', disabled: acting || !selected_workers.some(can_stop_now), on_click: () => { void run_selected_action('stop_now') } },
        { label: 'Zulassen', icon: 'check', disabled: acting || !selected_workers.some(worker => !worker.approved), on_click: () => { void run_selected_action('approve') } },
        { label: 'Entfernen', icon: 'trash', disabled: !selected_workers.some(can_delete), on_click: open_removal },
        { label: 'Bearbeiten', icon: 'pencil', disabled: selected_workers.length !== 1 || !workers, on_click: () => { const worker = selected_workers[0]; if (worker) edit(worker) } },
        { label: 'Version ausrollen', icon: 'rocket', disabled: none, on_click: not_available },
      ] : []}>
        {client_info?.available
          ? <a className="button" href={client_download_url} download><Icon name="download" size={14} /><span>Client herunterladen</span></a>
          : <button type="button" className="button" title={client_unavailable_title} disabled><Icon name="download" size={14} /><span>Client herunterladen</span></button>}
      </PageHeader>
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
        {editing_worker && availability_config && (
          <FormSection title="Verfügbarkeit">
            {editing_worker.id === 'server-worker'
              ? <p>Der Server-Worker ist immer verfügbar.</p>
              : <AvailabilityEditor value={availability_config} on_change={set_availability_config} disabled={saving} read_only={read_only_panel} />}
          </FormSection>
        )}
      </SlidePanel>
      <SlidePanel open={removal_workers !== null} title="Worker entfernen" icon="trash" on_close={close_removal}
        actions={[
          { label: 'Abbrechen', icon: 'close', on_click: close_removal, disabled: removing },
          { label: 'Entfernen', icon: 'trash', primary: true, on_click: confirm_removal, disabled: removing || !removal_workers?.length },
        ]}>
        {removal_workers?.length === 1
          ? <p>{removal_workers[0].name} wird entfernt. Meldet sich der Rechner erneut an, erscheint er als neuer Worker.</p>
          : <p>{removal_workers?.length ?? 0} Worker werden entfernt. Meldet sich ein Rechner erneut an, erscheint er als neuer Worker.</p>}
      </SlidePanel>
    </>
  )
}
