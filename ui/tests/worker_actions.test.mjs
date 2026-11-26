import assert from 'node:assert/strict'
import test from 'node:test'
import { availability_text, can_delete, can_start, can_stop_drain, can_stop_now } from '../src/lib/worker_actions.ts'

const worker = (overrides = {}) => ({
  id: 'worker-1',
  name: 'Worker 1',
  status: 'ready',
  approved: true,
  available: true,
  availability_reason: '',
  running_jobs: 0,
  ...overrides,
})

test('can_start requires approval and stopped or draining status', () => {
  assert.equal(can_start(worker({ status: 'stopped' })), true)
  assert.equal(can_start(worker({ status: 'draining' })), true)
  assert.equal(can_start(worker({ status: 'ready' })), false)
  assert.equal(can_start(worker({ status: 'stopping' })), false)
  assert.equal(can_start(worker({ status: 'stopped', approved: false })), false)
})

test('can_stop_drain is available only for ready workers', () => {
  assert.equal(can_stop_drain(worker({ status: 'ready' })), true)
  for (const status of ['draining', 'stopping', 'stopped']) {
    assert.equal(can_stop_drain(worker({ status })), false)
  }
})

test('can_stop_now is available for ready or draining workers', () => {
  assert.equal(can_stop_now(worker({ status: 'ready' })), true)
  assert.equal(can_stop_now(worker({ status: 'draining' })), true)
  assert.equal(can_stop_now(worker({ status: 'stopping' })), false)
  assert.equal(can_stop_now(worker({ status: 'stopped' })), false)
})

test('can_delete excludes the server worker and workers with running jobs', () => {
  assert.equal(can_delete(worker()), true)
  assert.equal(can_delete(worker({ id: 'server-worker' })), false)
  assert.equal(can_delete(worker({ running_jobs: 1 })), false)
})

test('availability_text prioritizes available, then reason, then nein', () => {
  assert.equal(availability_text(worker({ available: true, availability_reason: 'Grund' })), 'ja')
  assert.equal(availability_text(worker({ available: false, availability_reason: 'Zeitplan' })), 'Zeitplan')
  assert.equal(availability_text(worker({ available: false, availability_reason: '' })), 'nein')
})
