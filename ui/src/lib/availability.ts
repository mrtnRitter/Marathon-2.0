import type { AvailabilityConfig } from '../data/types'

export interface TimeWindow {
  start: string
  end: string
}

function valid_time(value: string, allow_end_of_day: boolean): boolean {
  if (allow_end_of_day && value === '24:00') return true
  return /^(?:[01]\d|2[0-3]):[0-5]\d$/.test(value)
}

export function parse_time_window(start: string, end: string): TimeWindow | null {
  if (!valid_time(start, false) || !valid_time(end, true)) return null
  return { start, end }
}

export function format_time_window(window: TimeWindow): string {
  return `${window.start}–${window.end}`
}

export function programs_from_lines(value: string): string[] {
  return value.split(/\r?\n/).map(program => program.trim()).filter(Boolean)
}

export function has_availability_rules(config: AvailabilityConfig): boolean {
  return config.schedule.enabled
    || config.idle.enabled
    || config.blocked_processes.some(program => program.trim().length > 0)
}
