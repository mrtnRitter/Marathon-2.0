import type { ScheduleInput, ScheduleUnit } from '../data/types'

export const schedule_unit_meta: Record<ScheduleUnit, { label: string; max: number }> = {
  minutes: { label: 'Minuten', max: 1439 },
  hours: { label: 'Stunden', max: 167 },
  days: { label: 'Tage', max: 365 },
  weeks: { label: 'Wochen', max: 52 },
  months: { label: 'Monate', max: 12 },
}

const interval_conversions: Partial<Record<ScheduleUnit, { unit: ScheduleUnit; divisor: number }>> = {
  minutes: { unit: 'hours', divisor: 60 },
  hours: { unit: 'days', divisor: 24 },
  days: { unit: 'weeks', divisor: 7 },
}

export function normalize_interval(every: number, unit: ScheduleUnit): { every: number; unit: ScheduleUnit } {
  if (!Number.isInteger(every) || every < 1) return { every, unit }
  let normalized_every = every
  let normalized_unit = unit
  let conversion = interval_conversions[normalized_unit]
  while (conversion && normalized_every % conversion.divisor === 0) {
    normalized_every /= conversion.divisor
    normalized_unit = conversion.unit
    conversion = interval_conversions[normalized_unit]
  }
  return { every: normalized_every, unit: normalized_unit }
}

export function schedule_error(schedule: ScheduleInput | null): string | null {
  if (schedule === null) return null
  if (!Number.isInteger(schedule.every)) return 'Intervall muss eine ganze Zahl sein'
  if (schedule.every < 1) return 'Intervall muss mindestens 1 sein'
  const normalized = normalize_interval(schedule.every, schedule.unit)
  const max = schedule_unit_meta[normalized.unit].max
  if (normalized.every > max) return `Intervall zu groß: höchstens ${max} ${schedule_unit_meta[normalized.unit].label}`
  if (!Number.isFinite(new Date(schedule.start).getTime())) return 'Erster Start fehlt'
  return null
}

const pad = (value: number) => String(value).padStart(2, '0')

export function to_local_input(iso: string): string {
  const date = new Date(iso)
  if (!Number.isFinite(date.getTime())) return ''
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

export function from_local_input(value: string): string {
  const date = new Date(value)
  return Number.isFinite(date.getTime()) ? date.toISOString() : ''
}

export function default_schedule(now: Date): ScheduleInput {
  const start = new Date(now)
  start.setMinutes(0, 0, 0)
  start.setHours(start.getHours() + 1)
  return { every: 1, unit: 'days', start: start.toISOString() }
}
