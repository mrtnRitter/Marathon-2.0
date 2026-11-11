import { FormField, FormSection } from '../../components/FormField'
import { Icon } from '../../components/Icon'
import type { WorkflowModule } from '../../data/types'
import type { ModuleNodeData } from './editor_types'
import { output_color } from '../../theme/status'

interface Props {
  data: ModuleNodeData
  on_change: (module: WorkflowModule) => void
  read_only?: boolean
}

/** Settings form generated from the module type description. */
export function ModuleSettingsPanel({ data, on_change, read_only = false }: Props) {
  const update_settings = (key: string, value: string | number | boolean) => {
    if (!read_only) on_change({ ...data.module, settings: { ...data.module.settings, [key]: value } })
  }

  return (
    <aside className="module-settings">
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
        </FormSection>
        <FormSection title={`Einstellungen · ${data.module_type.name}`}>
          {data.module_type.settings.map(spec => (
            <FormField key={spec.key} spec={spec} value={data.module.settings[spec.key]} disabled={read_only} on_change={value => update_settings(spec.key, value)} />
          ))}
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
