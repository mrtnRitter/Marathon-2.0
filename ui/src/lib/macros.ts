import type { MacroSummary, WorkflowOwner } from '../data/types'
// @ts-expect-error Node's native test runner resolves TypeScript imports by extension.
import { has_access } from './access.ts'

export function macro_owner_label(owner: WorkflowOwner | null): string {
  if (!owner) return 'Admin'
  return owner.display_name || owner.email
}

export function macro_usage_label(usage: number): string {
  if (usage === 0) return '–'
  return usage === 1 ? '1 Workflow' : `${usage} Workflows`
}

export function can_delete_macro(macro: MacroSummary): boolean {
  return has_access(macro.access, 'owner')
}

export function can_share_macro(macro: MacroSummary): boolean {
  return has_access(macro.access, 'owner')
}
