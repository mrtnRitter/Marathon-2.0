import { useLayoutEffect, useRef, useState, type PointerEvent } from 'react'
import { FormField, FormSection } from '../../components/FormField'
import { Icon } from '../../components/Icon'
import type { WorkflowModule } from '../../data/types'
import { parse_extensions, setting_errors } from '../../lib/module_settings'
import type { ModuleNodeData } from './editor_types'
import { output_color } from '../../theme/status'

interface Props {
  data: ModuleNodeData
  on_change: (module: WorkflowModule) => void
  read_only?: boolean
  width: number
  on_width_change: (width: number) => void
}

/** Settings form generated from the module type description. */
export function ModuleSettingsPanel({ data, on_change, read_only = false, width, on_width_change }: Props) {
  const drag = useRef<{ pointer_id: number; start_x: number; start_width: number } | null>(null)
  const description_ref = useRef<HTMLTextAreaElement>(null)
  const [resizing, set_resizing] = useState(false)
  const [focus_key, set_focus_key] = useState<string | null>(null)

  useLayoutEffect(() => {
    const element = description_ref.current
    if (!element) return
    element.style.height = 'auto'
    element.style.height = `${element.scrollHeight}px`
  }, [data.module.description, width])

  const end_drag = (event: PointerEvent<HTMLDivElement>) => {
    if (drag.current?.pointer_id !== event.pointerId) return
    drag.current = null
    set_resizing(false)
  }

  const errors = setting_errors(data.module_type, data.module.settings, focus_key)

  const update_settings = (key: string, value: string | number | boolean) => {
    if (!read_only) on_change({ ...data.module, settings: { ...data.module.settings, [key]: value } })
  }

  return (
    <aside className="module-settings" style={{ width }}>
      <div
        className={`module-settings-resizer${resizing ? ' active' : ''}`}
        role="separator"
        aria-orientation="vertical"
        aria-label="Breite ändern"
        onPointerDown={event => {
          if (drag.current || event.button !== 0) return
          event.preventDefault()
          event.currentTarget.setPointerCapture(event.pointerId)
          drag.current = { pointer_id: event.pointerId, start_x: event.clientX, start_width: width }
          set_resizing(true)
        }}
        onPointerMove={event => {
          if (!drag.current || drag.current.pointer_id !== event.pointerId) return
          on_width_change(drag.current.start_width + drag.current.start_x - event.clientX)
        }}
        onPointerUp={end_drag}
        onPointerCancel={end_drag}
        onLostPointerCapture={end_drag}
      />
      <header className="module-settings-header">
        <span className="module-node-icon"><Icon name={data.module_type.icon} size={14} /></span>
        <span>{data.module.name}</span>
        {read_only && <Icon name="lock" size={14} />}
      </header>
      <div className="module-settings-body">
        <p className="muted">{data.module_type.description}</p>
        <FormSection title="Allgemein">
          <div onBlur={read_only ? undefined : () => {
            if (!data.module.name.trim()) on_change({ ...data.module, name: data.module_type.name })
          }}>
            <FormField
              spec={{ key: 'module_name', label: 'Name', type: 'text' }}
              value={data.module.name}
              disabled={read_only}
              on_change={value => { if (!read_only) on_change({ ...data.module, name: String(value) }) }}
            />
          </div>
          <div className="form-field">
            <label htmlFor="field-module_description" className="form-field-label">Beschreibung</label>
            <div className="form-field-control">
              <textarea
                ref={description_ref}
                id="field-module_description"
                className="input module-settings-description"
                rows={1}
                value={data.module.description ?? ''}
                disabled={read_only}
                onChange={event => { if (!read_only) on_change({ ...data.module, description: event.target.value }) }}
              />
            </div>
          </div>
        </FormSection>
        <FormSection title={`Einstellungen · ${data.module_type.name}`}>
          {data.module_type.settings.map(spec => {
            const field = (
              <FormField
                spec={spec}
                value={data.module.settings[spec.key]}
                disabled={read_only}
                error={errors[spec.key]}
                on_change={value => {
                  if (spec.type === 'extensions') set_focus_key(spec.key)
                  update_settings(spec.key, value)
                }}
              />
            )
            if (spec.type !== 'extensions') return <div key={spec.key}>{field}</div>
            return (
              <div key={spec.key} onBlur={event => {
                if (event.relatedTarget && event.currentTarget.contains(event.relatedTarget as Node)) return
                if (read_only) return
                const raw = String(data.module.settings[spec.key] ?? spec.default ?? '')
                const normalized = parse_extensions(raw)
                if (normalized.invalid !== null) return
                const value = normalized.values.join(', ')
                if (value !== raw) update_settings(spec.key, value)
              }}>
                {field}
              </div>
            )
          })}
        </FormSection>
        <FormSection title="Ausgänge">
          {data.module_type.outputs.map(output => (
            <div key={output.id} className="module-settings-output">
              <b style={{ color: output_color(output.id) }}>{output.label}</b>
              <span className="dim">{output.description}</span>
            </div>
          ))}
        </FormSection>
      </div>
    </aside>
  )
}
