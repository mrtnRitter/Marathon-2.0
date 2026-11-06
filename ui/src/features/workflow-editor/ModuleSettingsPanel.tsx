import { useEffect, useState } from 'react'
import { FormField, FormSection } from '../../components/FormField'
import { SlidePanel } from '../../components/SlidePanel'
import type { FieldValue, WorkflowModule } from '../../data/types'
import type { ModuleNodeData } from './editor_types'
import { output_color } from '../../theme/status'

interface Props {
  data: ModuleNodeData | null
  on_close: () => void
  on_apply: (module: WorkflowModule) => void
}

/** Settings form generated from the module type description. */
export function ModuleSettingsPanel({ data, on_close, on_apply }: Props) {
  const [name, set_name] = useState('')
  const [settings, set_settings] = useState<Record<string, FieldValue>>({})
  useEffect(() => {
    set_name(data?.module.name ?? '')
    set_settings(data?.module.settings ?? {})
  }, [data])

  const apply = () => {
    if (data) on_apply({ ...data.module, name: name.trim() || data.module_type.name, settings })
    on_close()
  }

  return (
    <SlidePanel open={data !== null} title={data?.module.name ?? ''} icon={data?.module_type.icon} on_close={on_close}
      actions={[{ label: 'Abbrechen', icon: 'close', on_click: on_close }, { label: 'Übernehmen', icon: 'check', primary: true, on_click: apply }]}>
      {data && (
        <>
          <p className="muted">{data.module_type.description}</p>
          <FormSection title="Allgemein">
            <FormField spec={{ key: 'module_name', label: 'Name', type: 'text' }} value={name} on_change={value => set_name(String(value))} />
          </FormSection>
          <FormSection title={`Einstellungen · ${data.module_type.name}`}>
            {data.module_type.settings.map(spec => (
              <FormField key={spec.key} spec={spec} value={settings[spec.key]} on_change={value => set_settings(current => ({ ...current, [spec.key]: value }))} />
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
        </>
      )}
    </SlidePanel>
  )
}
