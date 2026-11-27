import type { MacroHull, WorkflowModule } from '../data/types'

export function upgrade_module(module: WorkflowModule, hull: MacroHull): { module: WorkflowModule; dropped: string[] } {
  const setting_keys = new Set(hull.settings.map(setting => setting.key))
  const settings = Object.fromEntries(Object.entries(module.settings).filter(([key]) => setting_keys.has(key)))
  const dropped = Object.keys(module.settings).filter(key => !setting_keys.has(key)).sort()

  return {
    module: {
      ...module,
      settings,
      macro_version: hull.version,
      latest_version: hull.version,
      update_available: false,
      hull,
    },
    dropped,
  }
}

export function dropped_message(dropped: string[]): string | null {
  return dropped.length === 0 ? null : `Entfallene Einstellungen: ${dropped.join(', ')}`
}
