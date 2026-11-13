import { notify } from '../components/Toast'
import { set_workflow_active } from '../data/api'
import type { Workflow } from '../data/types'

export function name_taken(workflows: Workflow[], name: string, own_id: string | null): boolean {
  const normalized_name = name.trim().toLowerCase()
  return normalized_name !== '' && workflows.some(workflow =>
    workflow.id !== own_id && workflow.name.trim().toLowerCase() === normalized_name,
  )
}

export async function set_workflows_active(workflows: Workflow[], active: boolean): Promise<number> {
  let count = 0
  for (const workflow of workflows) {
    if (workflow.active === active) continue
    await set_workflow_active(workflow.id, active)
    count += 1
  }
  if (count === 1) notify(active ? 'Workflow gestartet' : 'Workflow pausiert')
  else if (count > 1) notify(`${count} Workflows ${active ? 'gestartet' : 'pausiert'}`)
  return count
}
