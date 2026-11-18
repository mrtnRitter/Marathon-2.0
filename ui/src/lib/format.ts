const date_time = new Intl.DateTimeFormat('de-DE', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' })
const time = new Intl.DateTimeFormat('de-DE', { hour: '2-digit', minute: '2-digit', second: '2-digit' })

export const format_datetime = (iso: string) => date_time.format(new Date(iso))

const size_number = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 1, useGrouping: false })
const size_units = ['B', 'KB', 'MB', 'GB', 'TB']
const known_metadata = new Set(['source_path', 'size', 'mtime'])

/** Formats a byte count with binary units. */
export function format_size(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return String(bytes)
  let unit_index = 0
  let value = bytes
  while (value >= 1024 && unit_index < size_units.length - 1) {
    value /= 1024
    unit_index++
  }
  return `${size_number.format(value)} ${size_units[unit_index]}`
}

/** Formats known asset metadata first, followed by remaining keys in input order. */
export function format_metadata(metadata: Record<string, string>): [string, string][] {
  const entries: [string, string][] = []
  if (Object.prototype.hasOwnProperty.call(metadata, 'source_path')) entries.push(['Quellpfad', metadata.source_path])
  if (Object.prototype.hasOwnProperty.call(metadata, 'size')) {
    const value = metadata.size
    entries.push(['Größe', /^\d+$/.test(value) ? format_size(Number(value)) : value])
  }
  if (Object.prototype.hasOwnProperty.call(metadata, 'mtime')) {
    const value = metadata.mtime
    entries.push(['Datei geändert', Number.isFinite(Date.parse(value)) ? format_datetime(value) : value])
  }
  for (const [key, value] of Object.entries(metadata)) {
    if (known_metadata.has(key)) continue
    const label = key.replaceAll('_', ' ')
    entries.push([label ? `${label.charAt(0).toUpperCase()}${label.slice(1)}` : label, value])
  }
  return entries
}

export const format_time = (iso: string) => time.format(new Date(iso))

export function format_relative(iso: string): string {
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60_000)
  if (minutes < 1) return 'gerade eben'
  if (minutes < 60) return `vor ${minutes} min`
  if (minutes < 60 * 24) return `vor ${Math.round(minutes / 60)} h`
  return `vor ${Math.round(minutes / 60 / 24)} d`
}

export function format_duration(seconds: number): string {
  if (!seconds) return '–'
  const minutes = Math.floor(seconds / 60)
  return minutes ? `${minutes}m ${String(seconds % 60).padStart(2, '0')}s` : `${seconds}s`
}
