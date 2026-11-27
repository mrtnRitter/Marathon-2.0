import assert from 'node:assert/strict'
import test from 'node:test'
import { build_shares } from '../src/lib/share_form.ts'

test('build_shares returns no shares when sharing is disabled', () => {
  assert.deepEqual(build_shares(false, 'edit', { user_a: 'view' }, null), [])
})

test('build_shares includes only the All share when set', () => {
  assert.deepEqual(build_shares(true, 'use', {}, null), [
    { user_id: null, access: 'use' },
  ])
})

test('build_shares places All before users and preserves user order', () => {
  assert.deepEqual(build_shares(true, 'view', { user_b: 'edit', user_a: 'use' }, null), [
    { user_id: null, access: 'view' },
    { user_id: 'user_b', access: 'edit' },
    { user_id: 'user_a', access: 'use' },
  ])
})

test('build_shares omits the owner and users with empty access', () => {
  assert.deepEqual(build_shares(true, '', { owner: 'edit', private_user: '', shared_user: 'view' }, 'owner'), [
    { user_id: 'shared_user', access: 'view' },
  ])
})

test('build_shares includes user shares when owner_id is null', () => {
  assert.deepEqual(build_shares(true, '', { user_a: 'edit', user_b: '' }, null), [
    { user_id: 'user_a', access: 'edit' },
  ])
})
