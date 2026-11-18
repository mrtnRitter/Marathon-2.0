import assert from 'node:assert/strict'
import test from 'node:test'
import { merge_counters } from '../src/features/workflow-editor/live_counters.ts'

const counters = (waiting, running = 0, done = 0, failed = 0, discovered = 0) => ({ waiting, running, done, failed, discovered })
const node = (id, values) => ({ id, data: { module: { counters: values } } })

test('merge_counters returns null when counters are unchanged', () => {
  const nodes = [node('a', counters(1)), node('b', counters(2))]
  assert.equal(merge_counters(nodes, [{ id: 'a', counters: counters(1) }]), null)
})

test('merge_counters replaces only changed nodes', () => {
  const first = node('a', counters(1))
  const second = node('b', counters(2))
  const nodes = [first, second]
  const result = merge_counters(nodes, [
    { id: 'a', counters: counters(3) },
    { id: 'b', counters: counters(2) },
  ])
  assert.notEqual(result, nodes)
  assert.notEqual(result[0], first)
  assert.equal(result[1], second)
  assert.deepEqual(result[0].data.module.counters, counters(3))
})

test('merge_counters ignores modules without a matching node', () => {
  const nodes = [node('a', counters(1))]
  assert.equal(merge_counters(nodes, [{ id: 'missing', counters: counters(5) }]), null)
})
