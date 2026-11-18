import type { GraphImpact } from '../data/types'

export function impact_lines(impact: GraphImpact): string[] {
  const lines = ['Änderung betrifft laufende Assets.']
  const add_assets = (count: number, singular: string, plural: string) => {
    if (count > 0) lines.push(`${count} ${count === 1 ? singular : plural}`)
  }

  add_assets(impact.outcomes.rerouted, 'Asset wird ab dem vorherigen Modul neu verteilt', 'Assets werden ab dem vorherigen Modul neu verteilt')
  add_assets(impact.outcomes.done, 'Asset wird als erledigt abgeschlossen', 'Assets werden als erledigt abgeschlossen')
  add_assets(impact.outcomes.failed, 'Asset wird als fehlgeschlagen markiert', 'Assets werden als fehlgeschlagen markiert')
  if (impact.jobs > 0) lines.push(`${impact.jobs} ${impact.jobs === 1 ? 'Job wird abgebrochen' : 'Jobs werden abgebrochen'}`)
  if (impact.removed_modules.length > 0) lines.push(`Betroffene Module: ${impact.removed_modules.map(module => module.name).join(', ')}`)
  return lines
}
