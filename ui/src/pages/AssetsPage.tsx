import { useSearchParams } from 'react-router-dom'
import { PageHeader } from '../components/PageHeader'
import { get_workflows } from '../data/api'
import { use_data } from '../data/use_data'
import { AssetList } from '../features/assets/AssetList'
import { asset_statuses } from '../theme/status'

export function AssetsPage() {
  const [params, set_params] = useSearchParams()
  const workflow_id = params.get('workflow') ?? ''
  const status_param = params.get('status')
  const initial_status = asset_statuses.find(status => status === status_param)
  const { data: workflows = [] } = use_data(get_workflows)
  return (
    <>
      <PageHeader title="Assets">
        <label className="muted">
          Workflow{' '}
          <select className="input" style={{ width: 240 }} value={workflow_id}
            onChange={event => set_params(event.target.value ? { workflow: event.target.value } : {})}>
            <option value="">Alle Workflows</option>
            {workflows.map(workflow => <option key={workflow.id} value={workflow.id}>{workflow.name}</option>)}
          </select>
        </label>
      </PageHeader>
      <div className="page-content">
        <AssetList workflow_id={workflow_id || undefined} workflows={workflows} initial_status={initial_status} />
      </div>
    </>
  )
}
