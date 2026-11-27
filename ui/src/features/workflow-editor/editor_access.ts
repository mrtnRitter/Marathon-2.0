import type { Access, MacroSummary } from '../../data/types'

export function filter_usable_module_types(
  module_types: MacroSummary[],
  has_access: (access: Access | null | undefined, minimum: Access) => boolean,
): MacroSummary[] {
  return module_types.filter(module_type => has_access(module_type.access, 'use'))
}
