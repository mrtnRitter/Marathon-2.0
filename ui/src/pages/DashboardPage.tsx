import { useNavigate } from 'react-router-dom'
import { PageHeader } from '../components/PageHeader'
import { StatusBadge } from '../components/StatusBadge'
import { StatusBar } from '../components/StatusBar'
import { StatusTabs } from '../components/StatusTabs'
import { Button } from '../components/Toolbar'
import { get_dashboard } from '../data/api'
import type { Workflow } from '../data/types'
import { use_data } from '../data/use_data'
import { active_meta, asset_status_meta, asset_statuses } from '../theme/status'
import './DashboardPage.css'

function WorkflowRow({ workflow }: { workflow: Workflow }) {
  const navigate = useNavigate()
  const open = (tab: string) => navigate(`/workflows/${workflow.id}/${tab}`)
  return (
    <div className="dashboard-row" onDoubleClick={() => open('modules')}>
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
      <div className="dashboard-row-actions">
        <Button label="Module" icon="workflow" on_click={() => open('modules')} />
        <Button label="Assets" icon="files" on_click={() => open('assets')} />
      </div>
    </div>
  )
}

export function DashboardPage() {
  const { data } = use_data(get_dashboard)
  return (
    <>
      <PageHeader title="Dashboard" actions={[{ label: 'Hilfe', icon: 'help' }]}>
        {data && <span className="muted">Worker online: <b>{data.workers_online}</b> / {data.workers_total}</span>}
      </PageHeader>
      <div className="page-content">
        {!data ? <div className="loading">Lade</div> : (
          <>
            <StatusTabs items={asset_statuses.map(status => ({ id: status, ...asset_status_meta[status], count: data.totals[status] }))} />
            <section className="panel">
              <h2 className="panel-title">Workflows</h2>
              <div className="dashboard-rows">
                {data.workflows.map(workflow => <WorkflowRow key={workflow.id} workflow={workflow} />)}
              </div>
            </section>
          </>
        )}
      </div>
    </>
  )
}
