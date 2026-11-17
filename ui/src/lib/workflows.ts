import { useEffect, useRef } from 'react'
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

export function use_pausing_reload(workflows: Workflow[] | null | undefined, reload: () => void): void {
  const workflows_ref = useRef(workflows)
  const pause_started_at = useRef(new Map<string, { started_at: number; notified: boolean }>())
  workflows_ref.current = workflows

  useEffect(() => {
    const check_pausing = () => {
      const current = workflows_ref.current?.filter(workflow => workflow.pausing) ?? []
      const current_ids = new Set(current.map(workflow => workflow.id))
      for (const id of pause_started_at.current.keys()) {
        if (!current_ids.has(id)) pause_started_at.current.delete(id)
      }
      const now = Date.now()
      for (const workflow of current) {
        let pause = pause_started_at.current.get(workflow.id)
        if (!pause) {
          pause = { started_at: now, notified: false }
          pause_started_at.current.set(workflow.id, pause)
        } else if (!pause.notified && now - pause.started_at > 1000) {
          notify('Workflow wird gerade pausiert')
          pause.notified = true
        }
      }
    }

    check_pausing()
    if (!workflows_ref.current?.some(workflow => workflow.pausing)) return
    const timer = window.setInterval(() => {
      check_pausing()
      if (workflows_ref.current?.some(workflow => workflow.pausing)) reload()
    }, 1000)
    return () => window.clearInterval(timer)
  }, [workflows?.some(workflow => workflow.pausing), reload])
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
  let completed_count = 0
  for (const workflow of targets) {
    if (workflow.active === active) continue
    const updated = await set_workflow_active(workflow.id, active)
    count += 1
    if (active || !updated.pausing) completed_count += 1
  }
  if (completed_count === 1) notify(active ? 'Workflow gestartet' : 'Workflow pausiert')
  else if (completed_count > 1) notify(`${completed_count} Workflows ${active ? 'gestartet' : 'pausiert'}`)
  return count
}
