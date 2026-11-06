import type { ModuleType } from '../types'

const success = { id: 'success', label: 'Erfolg', description: 'Modul wurde erfolgreich ausgeführt' }
const failure = { id: 'failure', label: 'Fehler', description: 'Modul ist fehlgeschlagen; ohne Verbindung endet das Asset als fehlgeschlagen' }

export const module_types: ModuleType[] = [
  {
    id: 'input',
    name: 'Eingang',
    category: 'Eingang',
    icon: 'log-in',
    description: 'Startpunkt des Workflows. Jedes neue Asset der Liste beginnt hier.',
    has_input: false,
    outputs: [{ id: 'success', label: 'Weiter', description: 'Asset tritt in den Workflow ein' }],
    settings: [{ key: 'note', label: 'Notiz', type: 'text', default: '', help: 'Freitext, nur zur Dokumentation' }],
  },
  {
    id: 'wait',
    name: 'Warten',
    category: 'Test',
    icon: 'clock',
    description: 'Testmodul: wartet eine einstellbare Zeit und meldet den gewählten Ausgang.',
    has_input: true,
    outputs: [success, failure],
    settings: [
      { key: 'duration_s', label: 'Dauer (Sekunden)', type: 'number', default: 5, help: 'So lange läuft der Job' },
      { key: 'outcome', label: 'Ausgang', type: 'select', default: 'Erfolg', options: ['Erfolg', 'Fehler'], help: 'Welcher Ausgang am Ende gemeldet wird' },
    ],
  },
  {
    id: 'condition',
    name: 'Bedingung',
    category: 'Logik',
    icon: 'git-branch',
    description: 'Prüft ein Metadatenfeld und verzweigt nach Ja oder Nein.',
    has_input: true,
    outputs: [
      { id: 'yes', label: 'Ja', description: 'Bedingung ist erfüllt' },
      { id: 'no', label: 'Nein', description: 'Bedingung ist nicht erfüllt' },
      failure,
    ],
    settings: [
      { key: 'field', label: 'Feld', type: 'text', default: 'camera', help: 'Name des Metadatenfelds' },
      { key: 'operator', label: 'Vergleich', type: 'select', default: 'gleich', options: ['gleich', 'ungleich', 'enthält', 'Regex'] },
      { key: 'value', label: 'Wert', type: 'text', default: '' },
      { key: 'ignore_case', label: 'Groß-/Kleinschreibung ignorieren', type: 'boolean', default: true },
    ],
  },
  {
    id: 'move_files',
    name: 'Dateien bewegen',
    category: 'Dateien',
    icon: 'folder-input',
    description: 'Verschiebt oder kopiert die Dateien des Assets in einen Zielordner.',
    has_input: true,
    outputs: [success, failure],
    settings: [
      { key: 'target_path', label: 'Zielordner', type: 'text', default: '', help: 'Pfad, wie ihn der Worker sieht' },
      { key: 'mode', label: 'Modus', type: 'select', default: 'Verschieben', options: ['Verschieben', 'Kopieren'] },
      { key: 'overwrite', label: 'Vorhandene Dateien überschreiben', type: 'boolean', default: false },
    ],
  },
  {
    id: 'transcode',
    name: 'Transcodieren',
    category: 'Medien',
    icon: 'film',
    description: 'Erzeugt eine neue Mediendatei nach Preset.',
    has_input: true,
    outputs: [success, failure],
    settings: [
      { key: 'preset', label: 'Preset', type: 'select', default: 'ProRes 422 Proxy', options: ['ProRes 422 Proxy', 'ProRes 422 HQ', 'H.264 1080p', 'DNxHR LB'] },
      { key: 'hardware', label: 'Hardware-Beschleunigung', type: 'boolean', default: true },
      { key: 'suffix', label: 'Dateiendung', type: 'text', default: '_proxy' },
    ],
  },
]
