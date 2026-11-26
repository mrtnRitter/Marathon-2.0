import { useEffect, useRef, useState } from 'react'
import type { AvailabilityConfig, AvailabilityDay } from '../data/types'
import { format_time_window, has_availability_rules, parse_time_window, programs_from_lines } from '../lib/availability'
import { Icon } from './Icon'
import './AvailabilityEditor.css'

interface Props {
  value: AvailabilityConfig
  on_change: (value: AvailabilityConfig) => void
  disabled?: boolean
  read_only?: boolean
}

const weekdays: { key: AvailabilityDay; label: string }[] = [
  { key: 'mon', label: 'Montag' },
  { key: 'tue', label: 'Dienstag' },
  { key: 'wed', label: 'Mittwoch' },
  { key: 'thu', label: 'Donnerstag' },
  { key: 'fri', label: 'Freitag' },
  { key: 'sat', label: 'Samstag' },
  { key: 'sun', label: 'Sonntag' },
]

export function AvailabilityEditor({ value, on_change, disabled = false, read_only = false }: Props) {
  const locked = disabled || read_only
  const update_schedule = (enabled: boolean) => on_change({ ...value, schedule: { ...value.schedule, enabled } })
  const update_day = (day: AvailabilityDay, windows: { start: string; end: string }[]) => on_change({
    ...value,
    schedule: { ...value.schedule, days: { ...value.schedule.days, [day]: windows } },
  })
  const update_window = (day: AvailabilityDay, index: number, field: 'start' | 'end', text: string) => {
    const windows = [...(value.schedule.days[day] ?? [])]
    const next = { ...windows[index], [field]: text }
    const parsed = parse_time_window(next.start, next.end)
    windows[index] = parsed ?? next
    update_day(day, windows)
  }
  const add_window = (day: AvailabilityDay) => {
    const windows = value.schedule.days[day] ?? []
    if (windows.length >= 10) return
    update_day(day, [...windows, { start: '09:00', end: '17:00' }])
  }
  const remove_window = (day: AvailabilityDay, index: number) =>
    update_day(day, (value.schedule.days[day] ?? []).filter((_, item_index) => item_index !== index))
  const update_idle = (field: 'enabled' | 'minutes' | 'max_cpu_percent' | 'max_ram_percent' | 'max_gpu_percent', next: boolean | number) =>
    on_change({ ...value, idle: { ...value.idle, [field]: next } })
  const programs_ref = useRef(value.blocked_processes.join('\n'))
  const [programs_text, set_programs_text] = useState(programs_ref.current)
  useEffect(() => {
    const next_text = value.blocked_processes.join('\n')
    if (next_text !== programs_ref.current) set_programs_text(next_text)
    programs_ref.current = next_text
  }, [value.blocked_processes])

  return (
    <div className="availability-editor">
      {!has_availability_rules(value) && <p className="availability-always-note">Ohne Regeln ist der Worker immer verfügbar.</p>}
      <section className="availability-section">
        <h3>Zeitplan</h3>
        <label className="availability-switch-row">
          <input type="checkbox" checked={value.schedule.enabled} disabled={locked} onChange={event => update_schedule(event.target.checked)} />
          <span>Nur zu festen Zeiten verfügbar</span>
        </label>
        <div className="availability-days">
          {weekdays.map(day => {
            const windows = value.schedule.days[day.key] ?? []
            return (
              <div className="availability-day" key={day.key}>
                <strong>{day.label}</strong>
                <div className="availability-day-windows">
                  {windows.map((window, index) => (
                    <div className="availability-window" key={`${day.key}-${index}`}>
                      <input className="input" aria-label={`${day.label} Beginn`} placeholder="Beginn" value={window.start} disabled={locked} onChange={event => update_window(day.key, index, 'start', event.target.value)} />
                      <span aria-hidden>–</span>
                      <input className="input" aria-label={`${day.label} Ende`} placeholder="Ende" value={window.end} disabled={locked} onChange={event => update_window(day.key, index, 'end', event.target.value)} />
                      <span className="availability-window-preview">{parse_time_window(window.start, window.end) ? format_time_window(window) : ''}</span>
                      <button type="button" className="button availability-remove" aria-label={`${day.label} Zeitfenster entfernen`} title="Zeitfenster entfernen" disabled={locked} onClick={() => remove_window(day.key, index)}><Icon name="close" size={13} /></button>
                    </div>
                  ))}
                  <button type="button" className="button availability-add" disabled={locked || windows.length >= 10} onClick={() => add_window(day.key)}><Icon name="plus" size={13} /><span>Fenster hinzufügen</span></button>
                </div>
              </div>
            )
          })}
        </div>
        <p className="availability-help">Fenster über Mitternacht sind möglich, z. B. 19:00–07:00. 24:00 = bis Mitternacht. Es gilt die Uhrzeit des Rechners.</p>
      </section>

      <section className="availability-section">
        <h3>Programme</h3>
        <label className="availability-field-label" htmlFor="availability-programs">Nicht verfügbar, solange eines dieser Programme läuft</label>
        <textarea id="availability-programs" className="input availability-programs" rows={4} value={programs_text} disabled={locked} onChange={event => {
          const text = event.target.value
          const next_programs = programs_from_lines(text)
          set_programs_text(text)
          programs_ref.current = next_programs.join('\n')
          on_change({ ...value, blocked_processes: next_programs })
        }} />
        <p className="availability-help">Programmname ohne Pfad, z. B. Resolve.exe</p>
      </section>

      <section className="availability-section">
        <h3>Leerlauf</h3>
        <label className="availability-switch-row">
          <input type="checkbox" checked={value.idle.enabled} disabled={locked} onChange={event => update_idle('enabled', event.target.checked)} />
          <span>Erst bei Leerlauf starten</span>
        </label>
        <div className="availability-idle-fields">
          <label><span>Minuten ohne Tastatur und Maus</span><input className="input" type="number" min={1} max={1440} value={value.idle.minutes} disabled={locked} onChange={event => update_idle('minutes', Number(event.target.value))} /></label>
          <label><span>CPU höchstens (%)</span><input className="input" type="number" min={1} max={100} value={value.idle.max_cpu_percent} disabled={locked} onChange={event => update_idle('max_cpu_percent', Number(event.target.value))} /></label>
          <label><span>RAM höchstens (%)</span><input className="input" type="number" min={1} max={100} value={value.idle.max_ram_percent} disabled={locked} onChange={event => update_idle('max_ram_percent', Number(event.target.value))} /></label>
          <label><span>GPU höchstens (%)</span><input className="input" type="number" min={1} max={100} value={value.idle.max_gpu_percent} disabled={locked} onChange={event => update_idle('max_gpu_percent', Number(event.target.value))} /></label>
        </div>
        <p className="availability-help">Der Worker startet, wenn der Rechner so lange nicht benutzt wurde und die Last im Schnitt darunter lag. Eine Tastatur- oder Mauseingabe beendet die Verfügbarkeit; laufende Jobs laufen zu Ende. Ohne GPU-Messung gilt die GPU-Grenze als erfüllt.</p>
      </section>
    </div>
  )
}
