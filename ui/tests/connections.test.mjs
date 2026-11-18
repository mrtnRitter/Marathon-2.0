import assert from 'node:assert/strict'
import test from 'node:test'
import { is_valid_connection, last_incoming_edge, reconnectable_edge_ids } from '../src/features/workflow-editor/connections.ts'

const edge = (id, source, sourceHandle, target) => ({
  id,
  source,
  sourceHandle,
  target,
  targetHandle: 'in',
})

const connection = (source, sourceHandle, target) => ({ source, sourceHandle, target, targetHandle: 'in' })

test('last_incoming_edge selects the last edge in array order for its target', () => {
  const edges = [edge('first', 'a', 'success', 'c'), edge('other', 'b', 'error', 'd'), edge('last', 'b', 'success', 'c')]
  assert.equal(last_incoming_edge(edges, 'c')?.id, 'last')
  assert.equal(last_incoming_edge(edges, 'missing'), undefined)
})

test('is_valid_connection permits a reconnect when only its own edge occupies the output', () => {
  const edges = [edge('moving', 'a', 'success', 'b')]
  assert.equal(is_valid_connection(connection('a', 'success', 'c'), new Set(), edges), false)
  assert.equal(is_valid_connection(connection('a', 'success', 'c'), new Set(), edges, 'moving'), true)
})

test('is_valid_connection still rejects a duplicate output after excluding the moving edge', () => {
  const edges = [edge('moving', 'a', 'success', 'b'), edge('duplicate', 'a', 'success', 'c')]
  assert.equal(is_valid_connection(connection('a', 'success', 'd'), new Set(), edges, 'moving'), false)
  assert.equal(is_valid_connection(connection('a', 'error', 'd'), new Set(), edges), true)
})

test('is_valid_connection rejects self references and locked source or target modules', () => {
  assert.equal(is_valid_connection(connection('a', 'success', 'a'), new Set(), []), false)
  assert.equal(is_valid_connection(connection('a', 'success', 'b'), new Set(['a']), []), false)
  assert.equal(is_valid_connection(connection('a', 'success', 'b'), new Set(['b']), []), false)
})

test('reconnectable_edge_ids selects only the latest unlocked incoming edge per target', () => {
  const edges = [
    edge('old-a', 'a', 'success', 'x'),
    edge('last-a', 'b', 'success', 'x'),
    edge('old-y', 'c', 'success', 'y'),
    edge('locked-last', 'locked-source', 'error', 'y'),
    edge('last-z', 'd', 'error', 'z'),
    edge('locked-target', 'e', 'success', 'locked-target-module'),
  ]
  assert.deepEqual(reconnectable_edge_ids(edges, new Set(['locked-source', 'locked-target-module'])), new Set(['last-a', 'last-z']))
})
