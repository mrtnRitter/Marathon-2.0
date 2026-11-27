import assert from 'node:assert/strict'
import test from 'node:test'
import { add_block_to_definition, connection_from_edge, definition_to_flow, remove_connections_from_definition } from '../src/features/module-builder/builder_mapping.ts'
import { new_definition } from '../src/lib/macro_builder.ts'

function make_type(id, { name = id, has_input = true, outputs = [], settings = [] } = {}) {
  return { id, name, category: 'Test', icon: 'workflow', description: '', has_input, outputs, settings }
}

function make_block(id, type_id, position = { x: 0, y: 0 }, settings = {}) {
  return { id, type_id, name: id, position, settings }
}

test('definition_to_flow maps block identity, position, settings, and connections', () => {
  const definition = {
    ...new_definition(),
    blocks: [make_block('source', 'alpha', { x: 40, y: 80 }, { path: '/tmp/in' }), make_block('target', 'beta', { x: 300, y: 80 })],
    connections: [{ source_block_id: 'source', source_output_id: 'done', target_block_id: 'target' }],
  }
  const module_types = [make_type('alpha', { outputs: [{ id: 'done', label: 'Done' }] }), make_type('beta')]

  const flow = definition_to_flow(definition, module_types)

  assert.deepEqual(flow.nodes.map(node => [node.id, node.position, node.data.module.name, node.data.module.settings]), [
    ['source', { x: 40, y: 80 }, 'source', { path: '/tmp/in' }],
    ['target', { x: 300, y: 80 }, 'target', {}],
  ])
  assert.equal(flow.nodes[0].data.module_type, module_types[0])
  assert.equal(flow.nodes[0].data.module.counters.running, 0)
  assert.deepEqual(flow.edges.map(edge => [edge.source, edge.sourceHandle, edge.target, edge.targetHandle]), [
    ['source', 'done', 'target', 'in'],
  ])
})

test('connection_from_edge maps React Flow endpoints to a macro connection', () => {
  assert.deepEqual(connection_from_edge({ id: 'edge', source: 'a', sourceHandle: 'failed', target: 'b', targetHandle: 'in' }), {
    source_block_id: 'a', source_output_id: 'failed', target_block_id: 'b',
  })
})

test('add_block_to_definition selects an input and assigns matching free outputs', () => {
  const definition = { ...new_definition(), blocks: [make_block('copy_file', 'copy_file')] }
  const type = make_type('copy_file', { name: 'Kopieren', outputs: [{ id: 'success', label: 'Erfolg' }, { id: 'other', label: 'Weiter' }] })

  const result = add_block_to_definition(definition, type, [type], { x: 160, y: 120 })

  assert.equal(result.blocks[1].id, 'copy_file_2')
  assert.equal(result.blocks[1].name, 'Kopieren')
  assert.deepEqual(result.blocks[1].settings, {})
  assert.deepEqual(result.blocks[1].position, { x: 160, y: 120 })
  assert.equal(result.entry_block_id, 'copy_file_2')
  assert.deepEqual(result.outputs.find(output => output.id === 'success').sources, [
    { block_id: 'copy_file_2', output_id: 'success' },
  ])
  assert.deepEqual(result.outputs.find(output => output.id === 'error').sources, [])
  assert.deepEqual(definition.blocks, [make_block('copy_file', 'copy_file')])
})

test('add_block_to_definition leaves an existing entry and source entry unchanged', () => {
  const definition = { ...new_definition(), blocks: [make_block('start', 'source')], entry_block_id: 'start' }
  const module_types = [make_type('source'), make_type('step'), make_type('watch', { has_input: false })]

  const with_input = add_block_to_definition(definition, module_types[1], module_types, { x: 0, y: 0 })
  const without_input = add_block_to_definition(definition, module_types[2], module_types, { x: 0, y: 0 })
  const source_definition = { ...new_definition(), blocks: [] }
  const source = add_block_to_definition(source_definition, module_types[2], module_types, { x: 0, y: 0 })

  assert.equal(with_input.entry_block_id, 'start')
  assert.equal(without_input.entry_block_id, 'start')
  assert.equal(source.entry_block_id, null)
})


test('removing index-based edges keeps targeting the selected connection after earlier deletions', () => {
  const definition = {
    ...new_definition(),
    blocks: [make_block('a', 'alpha'), make_block('b', 'beta'), make_block('c', 'gamma'), make_block('d', 'delta')],
    connections: [
      { source_block_id: 'a', source_output_id: 'next', target_block_id: 'b' },
      { source_block_id: 'b', source_output_id: 'next', target_block_id: 'c' },
      { source_block_id: 'c', source_output_id: 'next', target_block_id: 'd' },
    ],
  }
  const types = ['alpha', 'beta', 'gamma', 'delta'].map(id => make_type(id, { outputs: [{ id: 'next', label: 'Weiter' }] }))
  const first_flow = definition_to_flow(definition, types)
  const after_first_delete = remove_connections_from_definition(definition, [connection_from_edge(first_flow.edges[0])])
  const second_flow = definition_to_flow(after_first_delete, types)
  const after_second_delete = remove_connections_from_definition(after_first_delete, [connection_from_edge(second_flow.edges[0])])

  assert.deepEqual(after_second_delete.connections, [
    { source_block_id: 'c', source_output_id: 'next', target_block_id: 'd' },
  ])
})

test('added blocks retain the module type default setting values', () => {
  const definition = new_definition()
  const type = make_type('source', { settings: [
    { key: 'enabled', label: 'Aktiv', type: 'boolean', default: true },
    { key: 'count', label: 'Anzahl', type: 'number', default: 3 },
    { key: 'optional', label: 'Optional', type: 'text' },
  ] })

  const result = add_block_to_definition(definition, type, [type], { x: 0, y: 0 })

  assert.deepEqual(result.blocks[0].settings, { enabled: true, count: 3 })
})
