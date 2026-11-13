import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { DataTable, type Column } from '../components/DataTable'
import { PageHeader } from '../components/PageHeader'
import { StatusBadge } from '../components/StatusBadge'
import { StatusBar } from '../components/StatusBar'
import { StatusTabs } from '../components/StatusTabs'
import { Button } from '../components/Toolbar'
import { get_dashboard } from '../data/api'
import { set_workflows_active } from '../lib/workflows'
import type { Worker, Workflow } from '../data/types'
import { use_data } from '../data/use_data'
import { active_meta, asset_status_meta, asset_statuses, is_online, online_meta } from '../theme/status'
import './DashboardPage.css'

function WorkflowRow({ workflow, on_changed }: { workflow: Workflow; on_changed: () => void }) {
  const navigate = useNavigate()
  const [busy, set_busy] = useState(false)
  const open = (tab: string) => navigate(`/workflows/${workflow.id}/${tab}`)
  const toggle = async () => {
    if (busy) return
    set_busy(true)
    try { await set_workflows_active([workflow], !workflow.active) } catch { /* request already reported */ } finally { set_busy(false); on_changed() }
  }
  return (
    <div className="dashboard-row" onClick={() => navigate(`/workflows/${workflow.id}/modules`)}>
      <div className="dashboard-row-head">
        <strong>{workflow.name}</strong>
        <StatusBadge meta={active_meta(workflow.active)} />
      </div>
      <div className="dashboard-row-desc dim">{workflow.description}</div>
      <StatusBar counts={workflow.asset_counts} />
      <div className="dashboard-row-counts">
        {asset_statuses.map(status => (
          <span key={status} style={{ color: asset_status_meta[status].color }}>
            <b>{workflow.asset_counts[status]}</b> {asset_status_meta[status].label}
          </span>
        ))}
      </div>
      <div className="dashboard-row-actions" onClick={event => event.stopPropagation()}>
        <Button label={workflow.active ? 'Pausieren' : 'Aktivieren'} icon={workflow.active ? 'pause' : 'play'} on_click={toggle} disabled={busy} />
        <Button label="Assets" icon="files" on_click={() => open('assets')} />
      </div>
    </div>
  )
}

const worker_columns: Column<Worker>[] = [
  { key: 'status', label: 'Status', render: row => <StatusBadge meta={online_meta(is_online(row))} />, sort_value: row => Number(is_online(row)) },
  { key: 'name', label: 'Name', render: row => <strong>{row.name}</strong> },
  { key: 'ip_address', label: 'IP-Adresse', render: row => <span className={row.ip_address === null ? 'dim' : undefined}>{row.ip_address ?? '–'}</span> },
]

export function DashboardPage() {
  const navigate = useNavigate()
  const { data, reload } = use_data(get_dashboard)
  return (
    <>
      <PageHeader title="Dashboard" />
      <div className="page-content">
        {!data ? <div className="loading">Lade</div> : (
          <>
            <section className="dashboard-section">
              <h2 className="dashboard-section-title">Workflows</h2>
              <StatusTabs items={asset_statuses.map(status => ({ id: status, ...asset_status_meta[status], count: data.totals[status] }))}
                on_change={status => navigate(`/assets?status=${status}`)} />
              <div className="panel">
                <div className="dashboard-rows">
                  {data.workflows.map(workflow => <WorkflowRow key={workflow.id} workflow={workflow} on_changed={reload} />)}
                </div>
              </div>
            </section>
            <section className="dashboard-section">
              <h2 className="dashboard-section-title">Worker</h2>
              <DataTable<Worker> columns={worker_columns} rows={data.workers} row_key={row => row.id} empty_text="Keine Worker registriert" />
            </section>
          </>
        )}
      </div>
    </>
  )
}
