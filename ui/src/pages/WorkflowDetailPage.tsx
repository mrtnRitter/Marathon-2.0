import { useState } from 'react'
import { Link, Navigate, useLocation, useNavigate, useParams } from 'react-router-dom'
import { PageHeader } from '../components/PageHeader'
import { IssueMark } from '../components/IssueMark'
import { StatusBadge } from '../components/StatusBadge'
import { Tabs } from '../components/Tabs'
import { get_workflow } from '../data/api'
import { set_workflows_active, use_pausing_notice } from '../lib/workflows'
import { use_data } from '../data/use_data'
import { AssetList } from '../features/assets/AssetList'
import { WorkflowCanvas } from '../features/workflow-editor/WorkflowCanvas'
import type { EditorMode } from '../features/workflow-editor/editor_types'
import { workflow_state_meta, asset_statuses } from '../theme/status'
import './WorkflowDetailPage.css'

const tabs = ['modules', 'assets']

export function WorkflowDetailPage() {
  const { workflow_id = '', tab = 'modules' } = useParams()
  const navigate = useNavigate()
  const location = useLocation()
  const initial_mode: EditorMode | undefined = (location.state as { mode?: string } | null)?.mode === 'edit' ? 'edit' : undefined
  const { data: workflow, loading, reload } = use_data(() => get_workflow(workflow_id), [workflow_id], { live: ['workflows', 'assets'] })
  use_pausing_notice(workflow ? [workflow] : undefined)
  const [busy, set_busy] = useState(false)
  const can_run = Boolean(workflow && ['admin', 'owner', 'edit', 'run'].includes(workflow.access))
  const can_edit = Boolean(workflow && ['admin', 'owner', 'edit'].includes(workflow.access))
  const toggle = async () => {
    if (!workflow || !can_run || busy) return
    set_busy(true)
    try { await set_workflows_active([workflow], !workflow.active) } catch { /* request already reported */ } finally { set_busy(false); reload() }
  }
  if (!tabs.includes(tab)) return <Navigate to={`/workflows/${workflow_id}/modules`} replace />
  const asset_total = workflow ? asset_statuses.reduce((sum, status) => sum + workflow.asset_counts[status], 0) : undefined
  return (
    <>
      <PageHeader
        breadcrumb={<><Link to="/workflows">Workflows</Link> / {workflow?.name ?? workflow_id}</>}
        title={<>{workflow?.name ?? (loading ? '…' : 'Workflow nicht gefunden')}{workflow && <><IssueMark issues={workflow.issues} /><StatusBadge meta={workflow_state_meta(workflow)} /></>}</>}
        actions={can_run && workflow ? [{ label: workflow.pausing ? 'Wird gestoppt' : workflow.active ? 'Stoppen' : 'Starten', icon: workflow.active || workflow.pausing ? 'stop' : 'play', on_click: toggle, disabled: busy || workflow.pausing }] : []}
      />
      <div className="workflow-tabs">
        <Tabs active={tab} on_change={id => navigate(`/workflows/${workflow_id}/${id}`)}
          items={[{ id: 'modules', label: 'Module', icon: 'workflow' }, { id: 'assets', label: 'Assets', icon: 'files', count: asset_total }]} />
      </div>
      {workflow && tab === 'modules' && <div className="page-content flush"><WorkflowCanvas workflow_id={workflow_id} initial_mode={can_edit ? initial_mode : 'monitor'} can_edit={can_edit} on_saved={reload} /></div>}
      {workflow && tab === 'assets' && <div className="page-content"><AssetList workflow_id={workflow_id} workflow_access={workflow.access} /></div>}
      {!workflow && !loading && <div className="page-content"><p className="muted">Kein Workflow mit dieser Kennung.</p></div>}
    </>
  )
}
