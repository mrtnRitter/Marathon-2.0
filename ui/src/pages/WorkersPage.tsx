import { useState } from 'react'
import { DataTable, type Column } from '../components/DataTable'
import { FormField, FormSection } from '../components/FormField'
import { PageHeader } from '../components/PageHeader'
import { SlidePanel } from '../components/SlidePanel'
import { StatusBadge } from '../components/StatusBadge'
import { StatusTabs } from '../components/StatusTabs'
import { not_available } from '../components/Toast'
import { get_workers } from '../data/api'
import type { FieldSpec, FieldValue, Worker, WorkerStatus } from '../data/types'
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

const worker_fields: FieldSpec[] = [
  { key: 'name', label: 'Name', type: 'text' },
  { key: 'enabled', label: 'Freigegeben', type: 'boolean', help: 'Nur freigegebene Worker bekommen Jobs' },
  { key: 'max_parallel', label: 'Parallele Jobs', type: 'number' },
  { key: 'capabilities', label: 'Fähigkeiten', type: 'text', help: 'Kommagetrennt, z. B. transcode, move_files' },
  { key: 'client_version', label: 'Client-Version', type: 'select', options: ['2.0.0', '1.9.4'], help: 'Version, die dieser Worker beim nächsten Lebenszeichen erhält' },
]

const status_tiles: WorkerStatus[] = ['ready', 'busy', 'new', 'updating', 'offline', 'locked']

export function WorkersPage() {
  const { data: workers } = use_data(get_workers)
  const [selected, set_selected] = useState<string[]>([])
  const [editing, set_editing] = useState<Record<string, FieldValue> | null>(null)
  const edit = (worker: Worker) => set_editing({
    name: worker.name, enabled: worker.status !== 'new' && worker.status !== 'locked', max_parallel: worker.max_parallel,
    capabilities: worker.capabilities.join(', '), client_version: worker.client_version,
  })
  const none = selected.length === 0
  return (
    <>
      <PageHeader title="Worker" actions={[
        { label: 'Bearbeiten', icon: 'pencil', disabled: selected.length !== 1, on_click: () => edit(workers!.find(worker => worker.id === selected[0])!) },
        { label: 'Freigeben', icon: 'check', disabled: none },
        { label: 'Sperren', icon: 'lock', disabled: none },
        { label: 'Version ausrollen', icon: 'rocket', disabled: none },
      ]} />
      <div className="page-content">
        {!workers ? <div className="loading">Lade</div> : (
          <>
            <StatusTabs items={status_tiles.map(status => ({ id: status, ...worker_status_meta[status], count: workers.filter(worker => worker.status === status).length }))} />
            <DataTable columns={columns} rows={workers} row_key={row => row.id} selected={selected} on_selection_change={set_selected} on_row_open={edit} />
          </>
        )}
      </div>
      <SlidePanel open={editing !== null} title={String(editing?.name ?? '')} icon="server" on_close={() => set_editing(null)}
        actions={[{ label: 'Abbrechen', icon: 'close', on_click: () => set_editing(null) }, { label: 'Speichern', icon: 'save', primary: true, on_click: not_available }]}>
        <FormSection title="Allgemein">
          {worker_fields.map(spec => (
            <FormField key={spec.key} spec={spec} value={editing?.[spec.key]} on_change={value => set_editing(current => ({ ...current, [spec.key]: value }))} />
          ))}
        </FormSection>
      </SlidePanel>
    </>
  )
}
