import { useState } from 'react'
import { Link, Navigate, useLocation, useNavigate, useParams } from 'react-router-dom'
import { PageHeader } from '../components/PageHeader'
import { StatusBadge } from '../components/StatusBadge'
import { Tabs } from '../components/Tabs'
import { get_workflow } from '../data/api'
import { set_workflows_active } from '../lib/workflows'
import { use_data } from '../data/use_data'
import { AssetList } from '../features/assets/AssetList'
import { WorkflowCanvas } from '../features/workflow-editor/WorkflowCanvas'
import type { EditorMode } from '../features/workflow-editor/editor_types'
import { active_meta, asset_statuses } from '../theme/status'
import './WorkflowDetailPage.css'

const tabs = ['modules', 'assets']

export function WorkflowDetailPage() {
  const { workflow_id = '', tab = 'modules' } = useParams()
  const navigate = useNavigate()
  const location = useLocation()
  const initial_mode: EditorMode | undefined = (location.state as { mode?: string } | null)?.mode === 'edit' ? 'edit' : undefined
  const { data: workflow, loading, reload } = use_data(() => get_workflow(workflow_id), [workflow_id])
  const [busy, set_busy] = useState(false)
  const toggle = async () => {
    if (!workflow || busy) return
    set_busy(true)
    try { await set_workflows_active([workflow], !workflow.active) } catch { /* request already reported */ } finally { set_busy(false); reload() }
  }
  if (!tabs.includes(tab)) return <Navigate to={`/workflows/${workflow_id}/modules`} replace />
  const asset_total = workflow ? asset_statuses.reduce((sum, status) => sum + workflow.asset_counts[status], 0) : undefined
  return (
    <>
      <PageHeader
        breadcrumb={<><Link to="/workflows">Workflows</Link> / {workflow?.name ?? workflow_id}</>}
        title={<>{workflow?.name ?? (loading ? '…' : 'Workflow nicht gefunden')}{workflow && <StatusBadge meta={active_meta(workflow.active)} />}</>}
        actions={[{ label: workflow?.active ? 'Pausieren' : 'Aktivieren', icon: workflow?.active ? 'pause' : 'play', on_click: toggle, disabled: !workflow || busy }]}
      />
      <div className="workflow-tabs">
        <Tabs active={tab} on_change={id => navigate(`/workflows/${workflow_id}/${id}`)}
          items={[{ id: 'modules', label: 'Module', icon: 'workflow' }, { id: 'assets', label: 'Assets', icon: 'files', count: asset_total }]} />
      </div>
      {workflow && tab === 'modules' && <div className="page-content flush"><WorkflowCanvas workflow_id={workflow_id} initial_mode={initial_mode} /></div>}
      {workflow && tab === 'assets' && <div className="page-content"><AssetList workflow_id={workflow_id} /></div>}
      {!workflow && !loading && <div className="page-content"><p className="muted">Kein Workflow mit dieser Kennung.</p></div>}
    </>
  )
}
