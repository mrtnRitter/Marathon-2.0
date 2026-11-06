import { useState } from 'react'
import { FormField, FormSection } from '../components/FormField'
import { PageHeader } from '../components/PageHeader'
import { not_available } from '../components/Toast'
import type { FieldSpec, FieldValue } from '../data/types'
import './SettingsPage.css'

const sections: { title: string; fields: FieldSpec[] }[] = [
  {
    title: 'Server',
    fields: [
      { key: 'host', label: 'Adresse', type: 'text', default: '127.0.0.1', help: 'Wird später aus config/server.toml gelesen' },
      { key: 'port', label: 'Port', type: 'number', default: 8080 },
      { key: 'live_channel', label: 'Live-Kanal', type: 'select', default: 'Server-Sent Events', options: ['Server-Sent Events', 'WebSocket'] },
    ],
  },
  {
    title: 'Rollout',
    fields: [
      { key: 'release_version', label: 'Freigegebene Client-Version', type: 'select', default: '2.0.0', options: ['2.0.0', '1.9.4'] },
      { key: 'auto_rollout', label: 'Neue Worker automatisch aktualisieren', type: 'boolean', default: true },
    ],
  },
  {
    title: 'Aufräumen',
    fields: [
      { key: 'history_days', label: 'Historie aufbewahren (Tage)', type: 'number', default: 90 },
      { key: 'log_days', label: 'Logs aufbewahren (Tage)', type: 'number', default: 30 },
    ],
  },
]

export function SettingsPage() {
  const [values, set_values] = useState<Record<string, FieldValue>>({})
  return (
    <>
      <PageHeader title="Einstellungen" actions={[{ label: 'Speichern', icon: 'save', primary: true, on_click: not_available }]} />
      <div className="page-content">
        <div className="panel settings-panel">
          <div className="panel-body">
            {sections.map(section => (
              <FormSection key={section.title} title={section.title}>
                {section.fields.map(spec => (
                  <FormField key={spec.key} spec={spec} value={values[spec.key]} on_change={value => set_values(current => ({ ...current, [spec.key]: value }))} />
                ))}
              </FormSection>
            ))}
          </div>
        </div>
      </div>
    </>
  )
}
