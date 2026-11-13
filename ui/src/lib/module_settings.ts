import type { FieldValue, ModuleType } from '../data/types'

export function parse_extensions(raw: string): { values: string[]; invalid: string | null } {
  const values: string[] = []
  const seen = new Set<string>()
  let invalid: string | null = null
  for (const entry of raw.split(',').map(value => value.trim()).filter(Boolean)) {
    const extension = (entry.startsWith('.') ? entry : `.${entry}`).toLowerCase()
    if (extension === '.' || /\s|[\/\\:*?"<>|]/.test(extension)) {
      invalid ??= entry
      continue
    }
    if (!seen.has(extension)) {
      seen.add(extension)
      values.push(extension)
    }
  }
  return { values, invalid }
}

export function validate_path(raw: string): string | null {
  const path = raw.trim()
  if (!path) return 'Ordner fehlt'
  if (path.startsWith('\\\\')) return /^\\\\[^\\/]+[\\/][^\\/]+/.test(path)
    ? null
    : "Netzpfad braucht Server und Freigabe: \\\\server\\freigabe"
  if (/^[A-Za-z]:[\\/]/.test(path) || path.startsWith('/')) return null
  return "Pfad muss absolut sein, z. B. D:\\Ingest oder \\\\server\\freigabe"
}

function _validate_priolist(value: unknown): string | null {
  if (!Array.isArray(value)) return 'Liste erwartet'
  const seen = new Set<string>()
  for (const [index, entry] of value.entries()) {
    if (typeof entry !== 'object' || entry === null || Array.isArray(entry)) return `Eintrag ${index + 1}: Name fehlt`
    const name = (entry as Record<string, unknown>).name
    if (typeof name !== 'string' || !name.trim()) return `Eintrag ${index + 1}: Name fehlt`
    const metadata = (entry as Record<string, unknown>).metadata
    if (metadata !== undefined && (metadata === null || typeof metadata !== 'object' || Array.isArray(metadata))) {
      return `Eintrag ${index + 1}: Metadaten ungültig`
    }
    const normalized_name = name.trim().toLowerCase()
    if (seen.has(normalized_name)) return `Eintrag ${index + 1}: Name doppelt: ${name.trim()}`
    seen.add(normalized_name)
  }
  return null
}

export function setting_errors(module_type: ModuleType, settings: Record<string, FieldValue>, focus_key?: string | null): Record<string, string> {
  const errors: Record<string, string> = {}
  const extension_values = new Map<string, string[]>()
  for (const spec of module_type.settings) {
    const value = settings[spec.key] ?? spec.default
    if (spec.type === 'priolist') {
      const raw_value: unknown = settings[spec.key] === undefined ? spec.default : settings[spec.key]
      const error = _validate_priolist(raw_value)
      if (error) errors[spec.key] = error
    } else if (spec.type === 'number') {
      const number = Number(value)
      if (value === '' || !Number.isFinite(number)) errors[spec.key] = 'Zahl erwartet'
      else if (spec.min !== undefined && number < spec.min) errors[spec.key] = `Mindestens ${spec.min}${spec.unit ? ` ${spec.unit}` : ''}`
      else if (spec.max !== undefined && number > spec.max) errors[spec.key] = `Höchstens ${spec.max}${spec.unit ? ` ${spec.unit}` : ''}`
    } else if (spec.type === 'path') {
      const error = validate_path(String(value ?? ''))
      if (error) errors[spec.key] = error
    } else if (spec.type === 'extensions') {
      const parsed = parse_extensions(String(value ?? ''))
      extension_values.set(spec.key, parsed.values)
      if (parsed.invalid !== null) errors[spec.key] = `Ungültige Endung: ${parsed.invalid}`
    }
  }

  const settings_by_key = new Map(module_type.settings.map(spec => [spec.key, spec]))
  const checked_pairs = new Set<string>()
  for (let index = 0; index < module_type.settings.length; index += 1) {
    const spec = module_type.settings[index]
    if (spec.type !== 'extensions' || !spec.exclusive_with) continue
    const other = settings_by_key.get(spec.exclusive_with)
    if (other?.type !== 'extensions') continue
    const pair = JSON.stringify([spec.key, other.key].sort())
    if (checked_pairs.has(pair)) continue
    checked_pairs.add(pair)
    if (errors[spec.key] || errors[other.key]) continue
    const spec_values = extension_values.get(spec.key) ?? []
    const other_values = new Set(extension_values.get(other.key) ?? [])
    if (!spec_values.some(value => other_values.has(value))) continue
    const other_index = module_type.settings.findIndex(item => item.key === other.key)
    const marked = focus_key === spec.key || focus_key === other.key
      ? (focus_key === spec.key ? spec : other)
      : (index > other_index ? spec : other)
    const unmarked = marked.key === spec.key ? other : spec
    const marked_values = extension_values.get(marked.key) ?? []
    const unmarked_values = new Set(extension_values.get(unmarked.key) ?? [])
    const conflicts = marked_values.filter(value => unmarked_values.has(value))
    const message = conflicts.length === 1 ? `${conflicts[0]} steht` : `${conflicts.join(', ')} stehen`
    errors[marked.key] = `${message} bereits in der ${unmarked.list_label ?? unmarked.label}`
  }
  return errors
}

export function has_setting_errors(module_type: ModuleType, settings: Record<string, FieldValue>): boolean {
  return Object.keys(setting_errors(module_type, settings)).length > 0
}
