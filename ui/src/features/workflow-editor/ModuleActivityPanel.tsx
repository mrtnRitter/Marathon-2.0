import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { DataTable, type Column } from '../../components/DataTable'
import { LogView } from '../../components/LogView'
import { SlidePanel } from '../../components/SlidePanel'
import { StatusBadge } from '../../components/StatusBadge'
import { ProgressBar } from '../../components/StatusBar'
import { Tabs } from '../../components/Tabs'
import { get_module_activity } from '../../data/api'
import type { Asset, Job, Worker } from '../../data/types'
import { use_data } from '../../data/use_data'
import { use_server_connected } from '../../data/use_connection'
import { format_relative } from '../../lib/format'
import { asset_status_meta, job_status_meta, shown_worker_status, step_status_meta, worker_status_meta } from '../../theme/status'
import type { ModuleNodeData } from './editor_types'
import { ModuleSettingsForm } from './ModuleSettingsPanel'

const job_columns: Column<Job>[] = [
  { key: 'id', label: 'Job' },
  { key: 'status', label: 'Status', render: row => <StatusBadge meta={job_status_meta[row.status]} /> },
  { key: 'worker', label: 'Worker', render: row => row.worker ?? <span className="dim">–</span> },
  { key: 'attempt', label: 'Versuch', align: 'right' },
  { key: 'progress', label: 'Fortschritt', render: row => <ProgressBar value={row.progress} /> },
  { key: 'asset_ids', label: 'Assets', render: row => row.asset_ids.length, align: 'right' },
]

const asset_columns: Column<Asset>[] = [
  { key: 'name', label: 'Name' },
  { key: 'status', label: 'Gesamtstatus', render: row => <StatusBadge meta={asset_status_meta[row.status]} /> },
  { key: 'step_status', label: 'Schritt', render: row => (row.step_status ? <StatusBadge meta={step_status_meta[row.step_status]} /> : '–') },
  { key: 'updated_at', label: 'Geändert', render: row => format_relative(row.updated_at) },
]

/** Module view in monitor mode: jobs, affected assets, involved workers and logs. */
export function ModuleActivityPanel({ workflow_id, data, on_close }: { workflow_id: string; data: ModuleNodeData; on_close: () => void }) {
  const navigate = useNavigate()
  const [tab, set_tab] = useState('jobs')
  const connected = use_server_connected()
  const { data: activity } = use_data(() => get_module_activity(workflow_id, data.module.id), [workflow_id, data.module.id], { live: ['jobs', 'job_assets', 'assets', 'workers', 'log_lines'] })
  const worker_columns = useMemo<Column<Worker>[]>(() => [
    { key: 'name', label: 'Name' },
    { key: 'status', label: 'Status', render: row => <StatusBadge meta={worker_status_meta[shown_worker_status(row, connected)]} /> },
    { key: 'running_jobs', label: 'Jobs', render: row => row.running_jobs },
    { key: 'client_version', label: 'Version' },
  ], [connected])
  return (
    <SlidePanel open title={data.module.name} icon={data.module_type.icon} on_close={on_close} width={760} click_through>
      <Tabs active={tab} on_change={set_tab} items={[
        { id: 'jobs', label: 'Jobs', icon: 'activity', count: activity?.jobs.length },
        { id: 'assets', label: 'Assets', icon: 'files', count: activity?.assets.length },
        { id: 'workers', label: 'Worker', icon: 'server', count: activity?.workers.length },
        { id: 'logs', label: 'Logs', icon: 'cpu' },
        { id: 'settings', label: 'Einstellungen', icon: 'settings' },
      ]} />
      <div className="module-activity-body">
        {tab === 'settings' ? <ModuleSettingsForm data={data} read_only /> : !activity ? <div className="loading">Lade</div> : {
          jobs: <DataTable columns={job_columns} rows={activity.jobs} row_key={row => row.id} empty_text="Keine Jobs an diesem Modul" />,
          assets: <DataTable columns={asset_columns} rows={activity.assets} row_key={row => row.id} on_row_open={row => navigate(`/assets/${row.id}`)} empty_text="Keine Assets an diesem Modul" />,
          workers: <DataTable columns={worker_columns} rows={activity.workers} row_key={row => row.id} empty_text="Keine Worker beteiligt" />,
          logs: <LogView lines={activity.logs} live={data.module.counters.running > 0} />,
        }[tab]}
      </div>
    </SlidePanel>
  )
}
