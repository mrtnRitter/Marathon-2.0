import type { Access, ShareAccess, WorkflowOwner } from '../data/types'

export const access_levels = ['view', 'use', 'edit', 'owner'] as const

export function has_access(access: Access | null | undefined, minimum: Access): boolean {
  if (access == null) return false
  return access_levels.indexOf(access) >= access_levels.indexOf(minimum)
}

export function access_label(access: Access): string {
  return {
    view: 'Ansicht',
    use: 'Verwendung',
    edit: 'Bearbeitung',
    owner: 'Besitzer',
  }[access]
}

export const share_options: { value: ShareAccess | ''; label: string }[] = [
  { value: '', label: '–' },
  { value: 'view', label: 'Zur Ansicht' },
  { value: 'use', label: 'Zur Verwendung' },
  { value: 'edit', label: 'Zur Bearbeitung' },
]

export function owner_label(owner: WorkflowOwner | null): string {
  return owner === null ? 'Admin' : owner.display_name || owner.email
}

export function is_lock_conflict(detail: string): boolean {
  return detail.startsWith('Wird gerade bearbeitet von ')
    || detail === 'Bearbeitungssperre fehlt oder wurde aufgehoben. Bitte neu laden.'
}
