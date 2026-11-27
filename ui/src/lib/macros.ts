import type { MacroSummary, WorkflowOwner } from '../data/types'

export function macro_owner_label(owner: WorkflowOwner | null): string {
  if (!owner) return 'Standard'
  return owner.display_name || owner.email
}

export function macro_usage_label(usage: number): string {
  if (usage === 0) return '–'
  return usage === 1 ? '1 Workflow' : `${usage} Workflows`
}

export function can_delete_macro(macro: MacroSummary): boolean {
  return macro.can_edit && macro.owner !== null
}
