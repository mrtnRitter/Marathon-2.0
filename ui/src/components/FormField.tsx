import type { ReactNode } from 'react'
import type { FieldSpec, FieldValue } from '../data/types'
import './FormField.css'

interface Props {
  spec: FieldSpec
  value: FieldValue | undefined
  on_change: (value: FieldValue) => void
}

/** One form row generated from a field description. */
export function FormField({ spec, value, on_change }: Props) {
  const current = value ?? spec.default ?? (spec.type === 'boolean' ? false : '')
  const id = `field-${spec.key}`
  const control = {
    text: <input id={id} className="input" value={String(current)} onChange={event => on_change(event.target.value)} />,
    number: <input id={id} className="input" type="number" value={Number(current)} onChange={event => on_change(Number(event.target.value))} />,
    select: (
      <select id={id} className="input" value={String(current)} onChange={event => on_change(event.target.value)}>
        {spec.options?.map(option => <option key={option}>{option}</option>)}
      </select>
    ),
    boolean: (
      <label className="form-switch">
        <input id={id} type="checkbox" checked={Boolean(current)} onChange={event => on_change(event.target.checked)} />
        <span className="form-switch-track" />
      </label>
    ),
  }[spec.type]
  return (
    <div className="form-field">
      <label htmlFor={id} className="form-field-label">{spec.label}</label>
      <div className="form-field-control">
        {control}
        {spec.help && <div className="form-field-help">{spec.help}</div>}
      </div>
    </div>
  )
}

export function FormSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="form-section">
      <h3>{title}</h3>
      {children}
    </section>
  )
}
