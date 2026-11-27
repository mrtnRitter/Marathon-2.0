import { useState, type FocusEvent } from 'react'
import { FormField, FormSection } from '../../components/FormField'
import type { FieldValue, MacroDefinition, MacroBlock, ModuleType } from '../../data/types'
import { parse_extensions, setting_errors } from '../../lib/module_settings'
import { set_setting_exposed } from '../../lib/macro_builder'

interface Props {
  block: MacroBlock
  module_type: ModuleType
  module_types: ModuleType[]
  definition: MacroDefinition
  disabled?: boolean
  on_definition_change: (definition: MacroDefinition) => void
}

export function BlockPanel({ block, module_type, module_types, definition, disabled = false, on_definition_change }: Props) {
  const [focus_key, set_focus_key] = useState<string | null>(null)
  const errors = setting_errors(module_type, block.settings, focus_key)
  const update_block = (next_block: MacroBlock) => on_definition_change({
    ...definition,
    blocks: definition.blocks.map(item => item.id === block.id ? next_block : item),
  })
  const update_setting = (key: string, value: FieldValue) => update_block({
    ...block,
    settings: { ...block.settings, [key]: value },
  })
  const update_exposed = (key: string, exposed: boolean) => on_definition_change(
    set_setting_exposed(definition, block.id, key, exposed, module_types),
  )
  const update_exposed_field = (key: string, field: 'label' | 'help', value: string) => on_definition_change({
    ...definition,
    settings: definition.settings.map(setting => setting.block_id === block.id && setting.setting_key === key
      ? { ...setting, [field]: value }
      : setting),
  })
  const normalize_extensions = (event: FocusEvent<HTMLDivElement>, key: string) => {
    if (event.relatedTarget && event.currentTarget.contains(event.relatedTarget as Node)) return
    const spec = module_type.settings.find(item => item.key === key)
    if (!spec) return
    const raw = String(block.settings[key] ?? spec.default ?? '')
    const parsed = parse_extensions(raw)
    if (parsed.invalid !== null) return
    const normalized = parsed.values.join(', ')
    if (normalized !== raw) update_setting(key, normalized)
  }

  return (
    <div className="module-builder-panel-body">
      <FormSection title="Baustein">
        <FormField
          spec={{ key: `block-name-${block.id}`, label: 'Name', type: 'text' }}
          value={block.name}
          disabled={disabled}
          on_change={value => update_block({ ...block, name: String(value) })}
        />
      </FormSection>
      <FormSection title={`Einstellungen · ${module_type.name}`}>
        {module_type.settings.map(spec => {
          const exposed = definition.settings.find(setting => setting.block_id === block.id && setting.setting_key === spec.key)
          const field = (
            <FormField
              spec={spec}
              value={block.settings[spec.key]}
              disabled={disabled}
              error={errors[spec.key]}
              on_change={value => {
                if (spec.type === 'extensions') set_focus_key(spec.key)
                update_setting(spec.key, value)
              }}
            />
          )
          return (
            <div key={spec.key} className="module-builder-setting">
              {spec.type === 'extensions' ? <div onBlur={event => normalize_extensions(event, spec.key)}>{field}</div> : field}
              <FormField
                spec={{ key: `expose-${block.id}-${spec.key}`, label: 'Im Modul einstellbar', type: 'boolean' }}
                value={Boolean(exposed)}
                disabled={disabled}
                on_change={value => update_exposed(spec.key, Boolean(value))}
              />
              {exposed && (
                <div className="module-builder-exposed-setting">
                  <FormField
                    spec={{ key: `setting-label-${block.id}-${spec.key}`, label: 'Beschriftung', type: 'text' }}
                    value={exposed.label}
                    disabled={disabled}
                    on_change={value => update_exposed_field(spec.key, 'label', String(value))}
                  />
                  <div className="form-field">
                    <label htmlFor={`setting-help-${block.id}-${spec.key}`} className="form-field-label">Hilfetext</label>
                    <div className="form-field-control">
                      <textarea
                        id={`setting-help-${block.id}-${spec.key}`}
                        className="input"
                        rows={2}
                        value={exposed.help}
                        disabled={disabled}
                        onChange={event => update_exposed_field(spec.key, 'help', event.target.value)}
                      />
                    </div>
                  </div>
                </div>
              )}
            </div>
          )
        })}
        {module_type.settings.length === 0 && <p className="dim">Keine Einstellungen vorhanden</p>}
      </FormSection>
    </div>
  )
}
