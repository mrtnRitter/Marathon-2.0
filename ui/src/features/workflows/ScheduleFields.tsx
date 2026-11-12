import { useEffect, useState } from 'react'
import { FormField } from '../../components/FormField'
import type { ScheduleInput, ScheduleUnit } from '../../data/types'
import { default_schedule, from_local_input, normalize_interval, schedule_error, schedule_unit_meta, to_local_input } from '../../lib/schedule'
import { format_datetime } from '../../lib/format'

export function ScheduleFields({ value, on_change, disabled, next_run_at }: { value: ScheduleInput | null; on_change: (value: ScheduleInput | null) => void; disabled?: boolean; next_run_at?: string | null }) {
  const [every_text, set_every_text] = useState(value ? String(value.every) : '')

  useEffect(() => {
    if (value && Number.isFinite(value.every) && value.every >= 1) set_every_text(String(value.every))
  }, [value?.every])

  const on_every_change = (text: string) => {
    set_every_text(text)
    if (!value) return
    on_change({ ...value, every: text.trim() === '' ? Number.NaN : Number(text) })
  }

  const normalize_current_interval = () => {
    if (!value) return
    const every = Number(every_text)
    if (!Number.isInteger(every) || every < 1) return
    const normalized = normalize_interval(every, value.unit)
    set_every_text(String(normalized.every))
    on_change({ ...value, ...normalized })
  }

  const change_unit = (unit: ScheduleUnit) => {
    if (!value) return
    const every = every_text.trim() === '' ? Number.NaN : Number(every_text)
    const normalized = normalize_interval(every, unit)
    if (Number.isInteger(every) && every >= 1) set_every_text(String(normalized.every))
    on_change({ ...value, ...normalized })
  }

  const error = schedule_error(value)

  return (
    <>
      <FormField
        spec={{ key: 'schedule_enabled', label: 'Zeitgesteuert starten', type: 'boolean', help: 'Aktiviert den Workflow im gewählten Rhythmus. Er pausiert sich selbst, sobald kein Asset mehr aussteht oder in Arbeit ist.' }}
        value={value !== null}
        disabled={disabled}
        on_change={enabled => on_change(enabled ? default_schedule(new Date()) : null)}
      />
      {value && (
        <>
          <div className="form-field">
            <label className="form-field-label" htmlFor="schedule-every">Alle</label>
            <div className="form-field-control" style={{ display: 'flex', gap: 'var(--space-2)' }}>
              <input id="schedule-every" className="input" type="number" min={1} max={schedule_unit_meta[value.unit].max} value={every_text} disabled={disabled} onChange={event => on_every_change(event.target.value)} onBlur={normalize_current_interval} />
              <select className="input" aria-label="Zeiteinheit" value={value.unit} disabled={disabled} onChange={event => change_unit(event.target.value as ScheduleUnit)}>
                {(Object.keys(schedule_unit_meta) as ScheduleUnit[]).map(unit => <option key={unit} value={unit}>{schedule_unit_meta[unit].label}</option>)}
              </select>
            </div>
          </div>
          <div className="form-field">
            <label className="form-field-label" htmlFor="schedule-start">Erster Start</label>
            <div className="form-field-control">
              <input id="schedule-start" className="input" type="datetime-local" value={to_local_input(value.start)} disabled={disabled} onChange={event => on_change({ ...value, start: from_local_input(event.target.value) })} />
            </div>
          </div>
          {error && <div className="form-field-help" style={{ color: 'var(--status-failed)' }}>{error}</div>}
          {!error && next_run_at && <div className="form-field-help">Nächster Start: {format_datetime(next_run_at)}</div>}
        </>
      )}
    </>
  )
}
