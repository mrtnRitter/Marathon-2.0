export function split_startable<T extends { active: boolean; issues: string[] }>(workflows: T[]): { startable: T[]; blocked: T[] } {
  const blocked = workflows.filter(workflow => !workflow.active && workflow.issues.length > 0)
  return { startable: workflows.filter(workflow => !blocked.includes(workflow)), blocked }
}

export function blocked_message(blocked: { name: string }[]): string | null {
  if (blocked.length === 0) return null
  if (blocked.length === 1) return `„${blocked[0].name}“ kann nicht gestartet werden: Einstellungen prüfen`
  return `${blocked.length} Workflows können nicht gestartet werden: Einstellungen prüfen`
}

export function issue_title(issues: string[]): string {
  return `Einstellungen prüfen: ${issues.join(', ')}`
}
