import assert from 'node:assert/strict'
import test from 'node:test'
import { has_setting_errors, parse_extensions, setting_errors, validate_path } from '../src/lib/module_settings.ts'

const priolist_type = {
  id: 'priolist',
  name: 'Prioliste',
  category: 'data',
  icon: 'list-ordered',
  description: '',
  has_input: false,
  outputs: [],
  settings: [{ key: 'entries', label: 'Einträge', type: 'priolist', default: [] }],
}

const watchfolder_type = {
  id: 'watchfolder',
  name: 'Watchfolder',
  category: 'input',
  icon: 'folder',
  description: '',
  has_input: false,
  outputs: [],
  settings: [
    { key: 'path', label: 'Ordner', type: 'path' },
    { key: 'include', label: 'Include', type: 'extensions', exclusive_with: 'exclude', list_label: 'Include-Liste' },
    { key: 'exclude', label: 'Exclude', type: 'extensions', exclusive_with: 'include', list_label: 'Exclude-Liste' },
    { key: 'wait_s', label: 'Wartezeit', type: 'number', default: 60, min: 5, max: 86400, unit: 's' },
    { key: 'recursive', label: 'Unterordner', type: 'boolean', default: false },
  ],
}

test('parse_extensions normalizes and stably deduplicates extensions', () => {
  assert.deepEqual(parse_extensions('.MXF, mov ,, .mxf'), { values: ['.mxf', '.mov'], invalid: null })
  assert.deepEqual(parse_extensions('.tar.gz'), { values: ['.tar.gz'], invalid: null })
  assert.equal(parse_extensions('.m x f').invalid, '.m x f')
  assert.equal(parse_extensions('.').invalid, '.')
  assert.equal(parse_extensions('a/b').invalid, 'a/b')
  assert.deepEqual(parse_extensions(''), { values: [], invalid: null })
})

test('validate_path accepts absolute local, network, and POSIX paths', () => {
  for (const path of ['D:\\Ingest', 'Z:/', '\\\\server\\share', '\\\\server\\share\\sub', '/mnt/ingest']) {
    assert.equal(validate_path(path), null, path)
  }
})

test('validate_path reports missing and malformed paths', () => {
  assert.equal(validate_path(''), 'Ordner fehlt')
  assert.equal(validate_path('   '), 'Ordner fehlt')
  assert.equal(validate_path("\\\\server"), "Netzpfad braucht Server und Freigabe: \\\\server\\freigabe")
  for (const path of ['Ingest', 'D:Ingest', '\\Ingest']) {
    assert.equal(validate_path(path), "Pfad muss absolut sein, z. B. D:\\Ingest oder \\\\server\\freigabe", path)
  }
})

test('setting_errors marks only the selected conflicting extension field', () => {
  const settings = { path: 'D:\\In', include: '.mxf, .mov', exclude: '.MOV' }
  assert.deepEqual(setting_errors(watchfolder_type, settings, 'include'), { include: '.mov steht bereits in der Exclude-Liste' })
  assert.deepEqual(setting_errors(watchfolder_type, settings, 'exclude'), { exclude: '.mov steht bereits in der Include-Liste' })
  assert.deepEqual(setting_errors(watchfolder_type, settings), { exclude: '.mov steht bereits in der Include-Liste' })
  assert.deepEqual(setting_errors(watchfolder_type, { path: 'D:\\In', include: '.mxf,.mov', exclude: '.mov,.mxf' }, 'exclude'), {
    exclude: '.mov, .mxf stehen bereits in der Include-Liste',
  })
})

test('setting_errors validates number bounds and default values', () => {
  assert.deepEqual(setting_errors(watchfolder_type, { path: 'D:\\In', wait_s: 4 }), { wait_s: 'Mindestens 5 s' })
  assert.deepEqual(setting_errors(watchfolder_type, { path: 'D:\\In', wait_s: 86401 }), { wait_s: 'Höchstens 86400 s' })
  assert.deepEqual(setting_errors(watchfolder_type, {}), { path: 'Ordner fehlt' })
  assert.deepEqual(setting_errors(watchfolder_type, { path: 'D:\\In', wait_s: Number.NaN }), { wait_s: 'Zahl erwartet' })
})

test('setting_errors rejects an empty number value', () => {
  assert.deepEqual(setting_errors(watchfolder_type, { path: 'D:\\In', wait_s: '' }), { wait_s: 'Zahl erwartet' })
})

test('setting_errors validates priolist values with backend messages', () => {
  assert.deepEqual(setting_errors(priolist_type, { entries: 'A' }), { entries: 'Liste erwartet' })
  assert.deepEqual(setting_errors(priolist_type, { entries: [{ name: 3 }] }), { entries: 'Eintrag 1: Name fehlt' })
  assert.deepEqual(setting_errors(priolist_type, { entries: [{ name: '   ' }] }), { entries: 'Eintrag 1: Name fehlt' })
  assert.deepEqual(setting_errors(priolist_type, { entries: [{ name: 'A', metadata: [] }] }), { entries: 'Eintrag 1: Metadaten ungültig' })
  assert.deepEqual(setting_errors(priolist_type, { entries: [{ name: 'A' }, { name: ' A ' }] }), { entries: 'Eintrag 2: Name doppelt: A' })
  assert.deepEqual(setting_errors(priolist_type, { entries: [{ name: 'A' }, { name: 'B', metadata: { rank: 2 } }] }), {})
})

test('has_setting_errors reflects setting validation', () => {
  assert.equal(has_setting_errors(watchfolder_type, {
    path: 'D:\\In', include: '.mxf', exclude: '.mov', wait_s: 60, recursive: true,
  }), false)
  assert.equal(has_setting_errors(watchfolder_type, {
    path: 'D:\\In', include: '.mxf', exclude: '.MXF', wait_s: 60, recursive: true,
  }), true)
})
