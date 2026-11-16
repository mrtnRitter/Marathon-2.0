import { notify } from '../components/Toast'
import { set_workflow_active } from '../data/api'
import type { Workflow } from '../data/types'
import { blocked_message, split_startable } from './workflow_issues'

export function name_taken(workflows: Workflow[], name: string, own_id: string | null): boolean {
  const normalized_name = name.trim().toLowerCase()
  return normalized_name !== '' && workflows.some(workflow =>
    workflow.id !== own_id && workflow.name.trim().toLowerCase() === normalized_name,
  )
}

export async function set_workflows_active(workflows: Workflow[], active: boolean): Promise<number> {
  let targets = workflows
  if (active) {
    const split = split_startable(workflows)
    targets = split.startable
    const message = blocked_message(split.blocked)
    if (message) notify(message)
  }

  let count = 0
  for (const workflow of targets) {
    if (workflow.active === active) continue
    await set_workflow_active(workflow.id, active)
    count += 1
  }
  if (count === 1) notify(active ? 'Workflow gestartet' : 'Workflow pausiert')
  else if (count > 1) notify(`${count} Workflows ${active ? 'gestartet' : 'pausiert'}`)
  return count
}
