import { useEffect, useState } from 'react'
import { SlidePanel } from '../../components/SlidePanel'
import { notify } from '../../components/Toast'
import { get_workflows, reprocess_assets } from '../../data/api'
import type { Workflow } from '../../data/types'
import { use_data } from '../../data/use_data'

interface Props {
  open: boolean
  asset_ids: string[]
  current_workflow_id?: string
  on_close: () => void
  on_done: (result_ids: string[]) => void
}

export function ReprocessPanel({ open, asset_ids, current_workflow_id, on_close, on_done }: Props) {
  const { data: workflows = [] } = use_data(() => (open ? get_workflows() : Promise.resolve([])), [open])
  const [target, set_target] = useState('')
  const [sending, set_sending] = useState(false)

  useEffect(() => {
    if (open) set_target('')
  }, [open])

  const submit = async () => {
    if (sending) return
    set_sending(true)
    try {
      const result = await reprocess_assets(asset_ids, target || null)
      const count = asset_ids.length
      notify(count === 1 ? 'Asset wird neu verarbeitet' : `${count} Assets werden neu verarbeitet`)
      on_done(result.asset_ids)
    } catch {
      return
    } finally {
      set_sending(false)
    }
  }

  const help = target
    ? 'Die Assets werden hier als erledigt beendet und im gewählten Workflow neu angelegt.'
    : 'Die Assets beginnen ihren Workflow von vorn.'

  return (
    <SlidePanel open={open} title="Erneut verarbeiten" icon="reprocess" on_close={on_close} actions={[
      { label: 'Abbrechen', icon: 'close', on_click: on_close },
      { label: 'Neu verarbeiten', icon: 'reprocess', primary: true, on_click: submit, disabled: sending },
    ]}>
      <div className="form-field">
        <label htmlFor="reprocess-target-workflow" className="form-field-label">Ziel-Workflow</label>
        <div className="form-field-control">
          <select id="reprocess-target-workflow" className="input" value={target} onChange={event => set_target(event.target.value)}>
            <option value="">Gleicher Workflow (von vorn)</option>
            {workflows.filter((workflow: Workflow) => workflow.id !== current_workflow_id).map(workflow => (
              <option key={workflow.id} value={workflow.id}>{workflow.name}</option>
            ))}
          </select>
          <div className="form-field-help">{help}</div>
        </div>
      </div>
    </SlidePanel>
  )
}
