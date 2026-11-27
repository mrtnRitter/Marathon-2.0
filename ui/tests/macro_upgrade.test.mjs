import assert from 'node:assert/strict'
import test from 'node:test'
import { dropped_message, upgrade_blind_module, upgrade_module } from '../src/lib/macro_upgrade.ts'
import { remove_edges_with_missing_outputs, to_graph_input, to_nodes } from '../src/features/workflow-editor/graph_mapping.ts'

const hull = {
  id: 'mc-test',
  version: 4,
  sort_order: 0,
  name: 'Module',
  category: 'Tools',
  icon: 'workflow',
  description: '',
  has_input: true,
  outputs: [],
  settings: [{ key: 'first' }, { key: 'second' }],
}

const module = {
  id: 'instance-1',
  type_id: 'mc-test',
  name: 'Instance',
  description: 'Description',
  position: { x: 10, y: 20 },
  settings: { second: 'kept-second', removed: 'drop-me', first: 'kept-first' },
  counters: { waiting: 0, running: 0, done: 0, failed: 0, discovered: 0 },
  locked: false,
  macro_version: 2,
  latest_version: 4,
  update_available: true,
}

test('upgrade_module keeps supported settings in their existing order and drops obsolete settings', () => {
  const result = upgrade_module(module, hull)

  assert.deepEqual(Object.keys(result.module.settings), ['second', 'first'])
  assert.deepEqual(result.module.settings, { second: 'kept-second', first: 'kept-first' })
  assert.deepEqual(result.dropped, ['removed'])
})

test('upgrade_module updates hull and version metadata without mutating the input', () => {
  const original = structuredClone(module)
  const result = upgrade_module(module, hull)

  assert.equal(result.module.macro_version, hull.version)
  assert.equal(result.module.latest_version, hull.version)
  assert.equal(result.module.update_available, false)
  assert.equal(result.module.hull, hull)
  assert.deepEqual(module, original)
})

test('dropped_message returns null for no dropped settings and lists dropped keys', () => {
  assert.equal(dropped_message([]), null)
  assert.equal(dropped_message(['a', 'b']), 'Entfallene Einstellungen: a, b')
})

test('to_nodes retains modules without a hull and preserves a pinned version with a palette fallback', () => {
  const pinned_module = { ...module, macro_version: 2 }
  const graph = { workflow_id: 'workflow-1', version: 3, modules: [pinned_module], connections: [] }

  const fallback_node = to_nodes(graph, [hull])[0]
  assert.equal(fallback_node.data.module_type.version, hull.version)
  assert.equal(to_graph_input(graph.version, [fallback_node], []).modules[0].macro_version, 2)

  const unresolved_node = to_nodes(graph, [])[0]
  assert.ok(unresolved_node)
  assert.equal(to_graph_input(graph.version, [unresolved_node], []).modules[0].macro_version, 2)
})

test('remove_edges_with_missing_outputs drops only upgraded module edges with removed outputs', () => {
  const edges = [
    { id: 'removed', source: 'instance-1', sourceHandle: 'old-output', target: 'target-1' },
    { id: 'kept', source: 'instance-1', sourceHandle: 'success', target: 'target-2' },
    { id: 'other', source: 'instance-2', sourceHandle: 'old-output', target: 'target-3' },
  ]
  const upgraded_hull = { outputs: [{ id: 'success' }] }

  assert.deepEqual(remove_edges_with_missing_outputs('instance-1', upgraded_hull.outputs, edges).map(edge => edge.id), ['kept', 'other'])
})


test('upgrade_blind_module advances only version metadata without mutating the module', () => {
  const blind_module = {
    ...module,
    type_id: '',
    settings: { secret: 'masked' },
    hull: { id: '', version: 2, name: '', settings: [], outputs: [] },
    latest_version: 5,
    update_available: true,
    blind: true,
  }
  const original = structuredClone(blind_module)
  const result = upgrade_blind_module(blind_module)

  assert.notEqual(result, blind_module)
  assert.equal(result.macro_version, 5)
  assert.equal(result.update_available, false)
  assert.equal(result.settings, blind_module.settings)
  assert.equal(result.hull, blind_module.hull)
  assert.deepEqual(blind_module, original)
})

test('upgrade_blind_module returns an unchanged copy when latest_version is absent', () => {
  const blind_module = { ...module, blind: true, latest_version: undefined }
  delete blind_module.latest_version
  const result = upgrade_blind_module(blind_module)

  assert.notEqual(result, blind_module)
  assert.deepEqual(result, blind_module)
})
