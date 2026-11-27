import { FormField, FormSection } from '../../components/FormField'
import { Icon, icon_names } from '../../components/Icon'
import type { MacroDefinition, ModuleType } from '../../data/types'
import { assign_output, free_outputs, output_assignment, output_id_from_label } from '../../lib/macro_builder'

interface Props {
  definition: MacroDefinition
  module_types: ModuleType[]
  disabled?: boolean
  on_definition_change: (definition: MacroDefinition) => void
}

export function MacroPanel({ definition, module_types, disabled = false, on_definition_change }: Props) {
  const update = (values: Partial<MacroDefinition>) => on_definition_change({ ...definition, ...values })
  const free = free_outputs(definition, module_types)
  const input_blocks = definition.blocks.filter(block => module_types.some(type => type.id === block.type_id && type.has_input))
  const add_output = () => {
    const id = output_id_from_label('Neuer Ausgang', definition.outputs.map(output => output.id))
    update({ outputs: [...definition.outputs, { id, label: 'Neuer Ausgang', description: '', sources: [] }] })
  }

  return (
    <div className="module-builder-panel-body">
      <FormSection title="Modul">
        <FormField spec={{ key: 'macro-name', label: 'Name', type: 'text' }} value={definition.name} disabled={disabled} on_change={value => update({ name: String(value) })} />
        <FormField spec={{ key: 'macro-category', label: 'Kategorie', type: 'text' }} value={definition.category} disabled={disabled} on_change={value => update({ category: String(value) })} />
        <FormField
          spec={{ key: 'macro-icon', label: 'Icon', type: 'select', options: icon_names }}
          value={definition.icon}
          disabled={disabled}
          addon={<span className="module-builder-icon-preview"><Icon name={definition.icon} /></span>}
          on_change={value => update({ icon: String(value) })}
        />
        <div className="form-field">
          <label htmlFor="macro-description" className="form-field-label">Beschreibung</label>
          <div className="form-field-control">
            <textarea id="macro-description" className="input" rows={3} value={definition.description} disabled={disabled} onChange={event => update({ description: event.target.value })} />
          </div>
        </div>
      </FormSection>
      <FormSection title="Eingang">
        <div className="form-field">
          <label htmlFor="macro-entry" className="form-field-label">Eingang</label>
          <div className="form-field-control">
            <select id="macro-entry" className="input" value={definition.entry_block_id ?? ''} disabled={disabled} onChange={event => update({ entry_block_id: event.target.value || null })}>
              <option value="">Kein Eingang (Quelle)</option>
              {input_blocks.map(block => <option key={block.id} value={block.id}>{block.name}</option>)}
            </select>
          </div>
        </div>
      </FormSection>
      <FormSection title="Ausgänge">
        {definition.outputs.map(output => (
          <div key={output.id} className="module-builder-output">
            <FormField
              spec={{ key: `output-label-${output.id}`, label: 'Beschriftung', type: 'text' }}
              value={output.label}
              disabled={disabled}
              on_change={value => update({ outputs: definition.outputs.map(item => item.id === output.id ? { ...item, label: String(value) } : item) })}
            />
            <div className="form-field">
              <label htmlFor={`output-description-${output.id}`} className="form-field-label">Beschreibung</label>
              <div className="form-field-control">
                <textarea
                  id={`output-description-${output.id}`}
                  className="input"
                  rows={2}
                  value={output.description}
                  disabled={disabled}
                  onChange={event => update({ outputs: definition.outputs.map(item => item.id === output.id ? { ...item, description: event.target.value } : item) })}
                />
              </div>
            </div>
            <button type="button" className="button" disabled={disabled} onClick={() => update({ outputs: definition.outputs.filter(item => item.id !== output.id) })}>Entfernen</button>
          </div>
        ))}
        <button type="button" className="button" disabled={disabled} onClick={add_output}>Ausgang hinzufügen</button>
      </FormSection>
      <FormSection title="Zuordnung">
        {free.map(item => (
          <div key={`${item.block_id}:${item.output_id}`} className="form-field">
            <label htmlFor={`output-assignment-${item.block_id}-${item.output_id}`} className="form-field-label">{item.block_name} › {item.label}</label>
            <div className="form-field-control">
              <select
                id={`output-assignment-${item.block_id}-${item.output_id}`}
                className="input"
                value={output_assignment(definition, item.block_id, item.output_id) ?? ''}
                disabled={disabled}
                onChange={event => on_definition_change(assign_output(definition, item.block_id, item.output_id, event.target.value || null, module_types))}
              >
                <option value="">– endet hier</option>
                {definition.outputs.map(output => <option key={output.id} value={output.id}>{output.label}</option>)}
              </select>
            </div>
          </div>
        ))}
        {free.length === 0 && <p className="dim">Keine freien Ausgänge</p>}
      </FormSection>
    </div>
  )
}
