const date_time = new Intl.DateTimeFormat('de-DE', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' })
const time = new Intl.DateTimeFormat('de-DE', { hour: '2-digit', minute: '2-digit', second: '2-digit' })

export const format_datetime = (iso: string) => date_time.format(new Date(iso))

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
