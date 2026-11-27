import type { MacroBlock, MacroDefinition, MacroOutputSource, ModuleOutput, ModuleType } from '../data/types'

export interface FreeOutput {
  block_id: string
  output_id: string
  block_name: string
  label: string
  data: boolean
}

export function new_definition(): MacroDefinition {
  return {
    name: 'Neues Modul',
    category: 'Eigene',
    icon: 'workflow',
    description: '',
    sort_order: 100,
    blocks: [],
    connections: [],
    entry_block_id: null,
    outputs: [
      { id: 'success', label: 'Erfolg', description: '', sources: [] },
      { id: 'error', label: 'Fehler', description: '', sources: [] },
    ],
    settings: [],
  }
}

export function unique_id(base: string, existing: string[]): string {
  if (!existing.includes(base)) return base
  let suffix = 2
  while (existing.includes(`${base}_${suffix}`)) suffix += 1
  return `${base}_${suffix}`
}

export function output_id_from_label(label: string, existing: string[]): string {
  const base = label.toLowerCase().replace(/[^a-z0-9]/gu, '_').slice(0, 60) || 'ausgang'
  if (!existing.includes(base)) return base

  let suffix = 2
  while (true) {
    const suffix_text = `_${suffix}`
    const candidate = `${base.slice(0, Math.max(0, 60 - suffix_text.length))}${suffix_text}`
    if (!existing.includes(candidate)) return candidate
    suffix += 1
  }
}

export function new_block_id(definition: MacroDefinition, type_id: string): string {
  return unique_id(type_id, definition.blocks.map(block => block.id))
}

function source_key(block_id: string, output_id: string): string {
  return JSON.stringify([block_id, output_id])
}

function type_by_id(module_types: ModuleType[]): Map<string, ModuleType> {
  return new Map(module_types.map(module_type => [module_type.id, module_type]))
}

function output_by_id(module_type: ModuleType | undefined, output_id: string): ModuleOutput | undefined {
  return module_type?.outputs.find(output => output.id === output_id)
}

export function free_outputs(definition: MacroDefinition, module_types: ModuleType[]): FreeOutput[] {
  const types = type_by_id(module_types)
  const internal_sources = new Set(definition.connections.map(connection => source_key(connection.source_block_id, connection.source_output_id)))
  const result: FreeOutput[] = []

  for (const block of definition.blocks) {
    const module_type = types.get(block.type_id)
    if (!module_type) continue
    for (const output of module_type.outputs) {
      if (typeof output.id !== 'string' || output.id.length === 0) continue
      if (internal_sources.has(source_key(block.id, output.id))) continue
      result.push({
        block_id: block.id,
        output_id: output.id,
        block_name: block.name,
        label: output.label,
        data: output.data === true,
      })
    }
  }
  return result
}

export function output_assignment(definition: MacroDefinition, block_id: string, output_id: string): string | null {
  const source_matches = (source: MacroOutputSource) => source.block_id === block_id && source.output_id === output_id
  return definition.outputs.find(output => output.sources.some(source_matches))?.id ?? null
}

export function assign_output(
  definition: MacroDefinition,
  block_id: string,
  output_id: string,
  target_output_id: string | null,
  module_types: ModuleType[],
): MacroDefinition {
  const block = definition.blocks.find(candidate => candidate.id === block_id)
  const types = type_by_id(module_types)
  const source_is_data = output_by_id(block ? types.get(block.type_id) : undefined, output_id)?.data === true
  const target = target_output_id === null ? undefined : definition.outputs.find(output => output.id === target_output_id)
  const target_has_data_source = target?.sources.some(source => {
    const source_block = definition.blocks.find(candidate => candidate.id === source.block_id)
    return output_by_id(source_block ? types.get(source_block.type_id) : undefined, source.output_id)?.data === true
  }) === true
  const replace_target_sources = source_is_data || target_has_data_source

  return {
    ...definition,
    outputs: definition.outputs.map(output => {
      const sources = output.sources.filter(source => source.block_id !== block_id || source.output_id !== output_id)
      if (output.id !== target_output_id) return { ...output, sources }
      return {
        ...output,
        sources: [
          ...(replace_target_sources ? [] : sources),
          { block_id, output_id },
        ],
      }
    }),
  }
}

export function set_setting_exposed(
  definition: MacroDefinition,
  block_id: string,
  setting_key: string,
  exposed: boolean,
  module_types: ModuleType[],
): MacroDefinition {
  if (!exposed) {
    return {
      ...definition,
      settings: definition.settings.filter(item => item.block_id !== block_id || item.setting_key !== setting_key),
    }
  }
  const block = definition.blocks.find(candidate => candidate.id === block_id)
  const setting = block ? type_by_id(module_types).get(block.type_id)?.settings.find(candidate => candidate.key === setting_key) : undefined
  if (!setting) return { ...definition }
  if (definition.settings.some(item => item.block_id === block_id && item.setting_key === setting_key)) return { ...definition }
  return {
    ...definition,
    settings: [...definition.settings, {
      key: unique_id(setting_key, definition.settings.map(item => item.key)),
      block_id,
      setting_key,
      label: setting.label,
      help: '',
    }],
  }
}

export function prune_definition(definition: MacroDefinition, module_types: ModuleType[]): MacroDefinition {
  const types = type_by_id(module_types)
  const blocks = new Map(definition.blocks.map(block => [block.id, block]))
  const connections = definition.connections
    .filter(connection => blocks.has(connection.source_block_id) && blocks.has(connection.target_block_id))
    .map(connection => ({ ...connection }))
  const internal_sources = new Set(connections.map(connection => source_key(connection.source_block_id, connection.source_output_id)))
  const outputs = definition.outputs.map(output => ({
    ...output,
    sources: output.sources.filter(source => {
      const block = blocks.get(source.block_id)
      const module_type = block ? types.get(block.type_id) : undefined
      return Boolean(module_type && output_by_id(module_type, source.output_id) && !internal_sources.has(source_key(source.block_id, source.output_id)))
    }).map(source => ({ ...source })),
  }))
  const settings = definition.settings
    .filter(setting => {
      const block = blocks.get(setting.block_id)
      const module_type = block ? types.get(block.type_id) : undefined
      return Boolean(module_type?.settings.some(candidate => candidate.key === setting.setting_key))
    })
    .map(setting => ({ ...setting }))
  const entry_block = definition.entry_block_id === null ? undefined : blocks.get(definition.entry_block_id)
  const entry_type = entry_block ? types.get(entry_block.type_id) : undefined

  return {
    ...definition,
    blocks: definition.blocks.map((block: MacroBlock) => ({ ...block })),
    connections,
    outputs,
    settings,
    entry_block_id: entry_type?.has_input ? definition.entry_block_id : null,
  }
}
