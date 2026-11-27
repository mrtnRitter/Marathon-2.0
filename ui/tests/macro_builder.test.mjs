import assert from 'node:assert/strict'
import test from 'node:test'
import {
  assign_output,
  free_outputs,
  new_block_id,
  new_definition,
  output_assignment,
  output_id_from_label,
  prune_definition,
  set_setting_exposed,
  unique_id,
} from '../src/lib/macro_builder.ts'

function make_block(id, type_id, name = id, settings = {}) {
  return { id, type_id, name, position: { x: 0, y: 0 }, settings }
}

function make_type(id, outputs = [], settings = [], has_input = true) {
  return { id, name: id, category: 'Test', icon: 'workflow', description: '', has_input, outputs, settings }
}

function make_output(id, sources = []) {
  return { id, label: id, description: '', sources }
}

function make_definition_with(blocks = [], outputs = []) {
  return { ...new_definition(), blocks, outputs }
}

test('new_definition returns the exact empty module definition', () => {
  assert.deepEqual(new_definition(), {
    name: 'Neues Modul',
    category: 'Eigene',
    icon: 'workflow',
    description: '',
    sort_order: 100,
    blocks: [],
    connections: [],
    entry_block_id: null,
    outputs: [
      { id: 'success', label: 'Erfolg', description: '', sources: [] },
      { id: 'error', label: 'Fehler', description: '', sources: [] },
    ],
    settings: [],
  })
})

test('unique_id and output_id_from_label normalize and disambiguate ids', () => {
  assert.equal(unique_id('step', []), 'step')
  assert.equal(unique_id('step', ['step', 'step_2']), 'step_3')
  assert.equal(output_id_from_label('Fertig ü', []), 'fertig__')
  assert.equal(output_id_from_label('Fertig ü', ['fertig__']), 'fertig___2')
  assert.equal(output_id_from_label('', []), 'ausgang')
  assert.equal(output_id_from_label('!!!', []), '___')
  assert.equal(output_id_from_label('A'.repeat(70), []).length, 60)
  assert.equal(output_id_from_label('a'.repeat(60), ['a'.repeat(60)]), `${'a'.repeat(58)}_2`)
  assert.equal(output_id_from_label('😀', []), '_')
})

test('new_block_id uses the type id and existing block ids', () => {
  const definition = make_definition_with([make_block('source', 'source'), make_block('source_2', 'other')])
  assert.equal(new_block_id(definition, 'source'), 'source_3')
  assert.equal(new_block_id(definition, 'fresh'), 'fresh')
})

test('free_outputs preserves block and type output order and excludes internal connections', () => {
  const definition = make_definition_with(
    [make_block('first', 'alpha', 'Erster'), make_block('second', 'beta', 'Zweiter'), make_block('unknown', 'missing')],
    [],
  )
  definition.connections = [{ source_block_id: 'first', source_output_id: 'done', target_block_id: 'second' }]
  const module_types = [
    make_type('alpha', [
      { id: 'done', label: 'Fertig' },
      { id: 'payload', label: 'Daten', data: true },
      { label: 'Ohne ID' },
    ]),
    make_type('beta', [{ id: 'end', label: 'Ende' }]),
  ]
  assert.deepEqual(free_outputs(definition, module_types), [
    { block_id: 'first', output_id: 'payload', block_name: 'Erster', label: 'Daten', data: true },
    { block_id: 'second', output_id: 'end', block_name: 'Zweiter', label: 'Ende', data: false },
  ])
})

test('output_assignment finds a source assignment or returns null', () => {
  const definition = make_definition_with([], [
    make_output('success', [{ block_id: 'a', output_id: 'done' }]),
    make_output('error'),
  ])
  assert.equal(output_assignment(definition, 'a', 'done'), 'success')
  assert.equal(output_assignment(definition, 'b', 'done'), null)
})

test('assign_output moves sources, removes null assignments, and replaces data sources', () => {
  const module_types = [
    make_type('regular', [{ id: 'done', label: 'Fertig' }]),
    make_type('payload', [{ id: 'data', label: 'Daten', data: true }]),
  ]
  const definition = make_definition_with(
    [make_block('a', 'regular'), make_block('b', 'regular'), make_block('c', 'regular')],
    [
      make_output('first', [{ block_id: 'a', output_id: 'done' }, { block_id: 'b', output_id: 'done' }]),
      make_output('second', [{ block_id: 'c', output_id: 'done' }]),
    ],
  )
  const moved = assign_output(definition, 'a', 'done', 'second', module_types)
  assert.deepEqual(moved.outputs.map(output => output.sources), [
    [{ block_id: 'b', output_id: 'done' }],
    [{ block_id: 'c', output_id: 'done' }, { block_id: 'a', output_id: 'done' }],
  ])
  const removed = assign_output(moved, 'a', 'done', null, module_types)
  assert.deepEqual(removed.outputs[1].sources, [{ block_id: 'c', output_id: 'done' }])

  const data_definition = make_definition_with(
    [make_block('regular', 'regular'), make_block('payload_a', 'payload'), make_block('payload_b', 'payload')],
    [make_output('result', [
      { block_id: 'regular', output_id: 'done' },
      { block_id: 'payload_a', output_id: 'data' },
    ])],
  )
  const data_assigned = assign_output(data_definition, 'payload_b', 'data', 'result', module_types)
  assert.deepEqual(data_assigned.outputs[0].sources, [{ block_id: 'payload_b', output_id: 'data' }])
  const regular_assigned = assign_output(data_assigned, 'regular', 'done', 'result', module_types)
  assert.deepEqual(regular_assigned.outputs[0].sources, [{ block_id: 'regular', output_id: 'done' }])
})

test('set_setting_exposed adds unique module keys and removes only the selected exposure', () => {
  const definition = make_definition_with([make_block('a', 'alpha'), make_block('b', 'beta')])
  const module_types = [
    make_type('alpha', [], [{ key: 'path', label: 'Dateipfad', type: 'text' }]),
    make_type('beta', [], [{ key: 'path', label: 'Zielpfad', type: 'text' }]),
  ]
  const first = set_setting_exposed(definition, 'a', 'path', true, module_types)
  const unchanged_first = set_setting_exposed(first, 'a', 'path', true, module_types)
  const second = set_setting_exposed(unchanged_first, 'b', 'path', true, module_types)
  assert.deepEqual(second.settings, [
    { key: 'path', block_id: 'a', setting_key: 'path', label: 'Dateipfad', help: '' },
    { key: 'path_2', block_id: 'b', setting_key: 'path', label: 'Zielpfad', help: '' },
  ])
  assert.deepEqual(set_setting_exposed(second, 'b', 'path', false, module_types).settings, [second.settings[0]])
  const stale = { ...definition, settings: [{ key: 'stale', block_id: 'a', setting_key: 'missing', label: 'Alt', help: '' }] }
  const unknown = set_setting_exposed(stale, 'a', 'missing', false, module_types)
  assert.notStrictEqual(unknown, stale)
  assert.deepEqual(unknown.settings, [])
  assert.deepEqual(stale.settings, [{ key: 'stale', block_id: 'a', setting_key: 'missing', label: 'Alt', help: '' }])
})

test('prune_definition drops stale references and keeps fixed block settings', () => {
  const definition = make_definition_with(
    [make_block('a', 'alpha', 'A', { fixed: 'keep' }), make_block('c', 'beta')],
    [make_output('result', [
      { block_id: 'a', output_id: 'done' },
      { block_id: 'a', output_id: 'gone' },
      { block_id: 'missing', output_id: 'done' },
      { block_id: 'c', output_id: 'end' },
    ])],
  )
  definition.connections = [
    { source_block_id: 'missing', source_output_id: 'done', target_block_id: 'c' },
    { source_block_id: 'a', source_output_id: 'done', target_block_id: 'missing' },
    { source_block_id: 'a', source_output_id: 'done', target_block_id: 'c' },
  ]
  definition.settings = [
    { key: 'one', block_id: 'a', setting_key: 'valid', label: 'Gültig', help: '' },
    { key: 'two', block_id: 'a', setting_key: 'stale', label: 'Veraltet', help: '' },
    { key: 'three', block_id: 'missing', setting_key: 'valid', label: 'Fehlt', help: '' },
  ]
  definition.entry_block_id = 'c'
  const result = prune_definition(definition, [
    make_type('alpha', [{ id: 'done', label: 'Fertig' }], [{ key: 'valid', label: 'Gültig', type: 'text' }]),
    make_type('beta', [{ id: 'end', label: 'Ende' }], [], false),
  ])
  assert.deepEqual(result.connections, [definition.connections[2]])
  assert.deepEqual(result.outputs[0].sources, [{ block_id: 'c', output_id: 'end' }])
  assert.deepEqual(result.settings, [definition.settings[0]])
  assert.equal(result.entry_block_id, null)
  assert.deepEqual(result.blocks[0].settings, { fixed: 'keep' })
})

test('builder helpers do not mutate their inputs', () => {
  const definition = make_definition_with([make_block('a', 'alpha')], [make_output('success')])
  definition.connections = []
  const module_types = [make_type('alpha', [{ id: 'done', label: 'Fertig' }], [{ key: 'path', label: 'Pfad', type: 'text' }])]
  const before_definition = structuredClone(definition)
  const before_types = structuredClone(module_types)
  const existing = ['step']
  const before_existing = structuredClone(existing)
  free_outputs(definition, module_types)
  assign_output(definition, 'a', 'done', 'success', module_types)
  set_setting_exposed(definition, 'a', 'path', true, module_types)
  prune_definition(definition, module_types)
  unique_id('step', existing)
  output_id_from_label('Step', existing)
  new_block_id(definition, 'step')
  output_assignment(definition, 'a', 'done')
  assert.deepEqual(definition, before_definition)
  assert.deepEqual(module_types, before_types)
  assert.deepEqual(existing, before_existing)
})
