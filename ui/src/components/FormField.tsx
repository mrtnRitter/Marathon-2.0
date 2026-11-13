import { useState, type ReactNode } from 'react'
import { FolderDialog } from './FolderDialog'
import { Icon } from './Icon'
import type { FieldSpec, FieldValue } from '../data/types'
import './FormField.css'

interface Props {
  spec: FieldSpec
  value: FieldValue | undefined
  on_change: (value: FieldValue) => void
  disabled?: boolean
  error?: string | null
  addon?: ReactNode
}

/** One form row generated from a field description. */
export function FormField({ spec, value, on_change, disabled = false, error, addon }: Props) {
  const current = value ?? spec.default ?? (spec.type === 'boolean' ? false : '')
  const [folder_dialog_open, set_folder_dialog_open] = useState(false)
  const id = `field-${spec.key}`
  const has_error = Boolean(error)
  const control_class = has_error ? 'input input-error' : 'input'
  const control = {
    text: <input id={id} className={control_class} aria-invalid={has_error || undefined} value={String(current)} disabled={disabled} onChange={event => on_change(event.target.value)} />,
    path: <input id={id} className={control_class} aria-invalid={has_error || undefined} value={String(current)} disabled={disabled} onChange={event => on_change(event.target.value)} placeholder="D:\Ingest oder \\server\freigabe" />,
    extensions: <input id={id} className={control_class} aria-invalid={has_error || undefined} value={String(current)} disabled={disabled} onChange={event => on_change(event.target.value)} placeholder=".mxf, .mov" />,
    number: <input id={id} className={control_class} aria-invalid={has_error || undefined} type="number" min={spec.min} max={spec.max} value={current === '' ? '' : String(current)} disabled={disabled} onChange={event => on_change(event.target.value === '' ? '' : Number(event.target.value))} />,
    select: (
      <select id={id} className={control_class} aria-invalid={has_error || undefined} value={String(current)} disabled={disabled} onChange={event => on_change(event.target.value)}>
        {spec.options?.map(option => <option key={option}>{option}</option>)}
      </select>
    ),
    boolean: (
      <label className="form-switch">
        <input id={id} type="checkbox" checked={Boolean(current)} disabled={disabled} onChange={event => on_change(event.target.checked)} />
        <span className="form-switch-track" />
      </label>
    ),
  }[spec.type]
  const unit = spec.type === 'number' ? spec.unit : undefined
  const has_addon = addon !== undefined && addon !== null && addon !== false
  const folder_button = spec.type === 'path' && !disabled ? (
    <button type="button" className="button" title="Ordner wählen" aria-label="Ordner wählen" onClick={() => set_folder_dialog_open(true)}>
      <Icon name="folder-open" />
    </button>
  ) : null
  return (
    <div className="form-field">
      <label htmlFor={id} className="form-field-label">{spec.label}</label>
      <div className="form-field-control">
        {has_addon || unit || folder_button ? (
          <div className="form-field-row">
            {control}
            {unit && <span className="form-field-unit">{unit}</span>}
            {folder_button}
            {addon}
          </div>
        ) : control}
        {spec.type === 'path' && <FolderDialog open={folder_dialog_open} initial_path={String(current)} on_select={path => on_change(path)} on_close={() => set_folder_dialog_open(false)} />}
        {has_error && <div className="form-field-error">{error}</div>}
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
