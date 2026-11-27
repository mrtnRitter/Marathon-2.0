import test from 'node:test'
import assert from 'node:assert/strict'
import { macro_owner_label, macro_usage_label, can_delete_macro, can_share_macro } from '../src/lib/macros.ts'

const macro = (access, owner = null) => ({ access, owner })

test('macro_owner_label handles admin, display name, and email fallbacks', () => {
  assert.equal(macro_owner_label(null), 'Admin')
  assert.equal(macro_owner_label({ id: 'u1', display_name: 'Alex', email: 'alex@example.test' }), 'Alex')
  assert.equal(macro_owner_label({ id: 'u1', display_name: '', email: 'alex@example.test' }), 'alex@example.test')
})

test('macro_usage_label uses the required singular and plural labels', () => {
  assert.equal(macro_usage_label(0), '–')
  assert.equal(macro_usage_label(1), '1 Workflow')
  assert.equal(macro_usage_label(4), '4 Workflows')
})

test('macro deletion and sharing require owner access, not a user owner', () => {
  assert.equal(can_delete_macro(macro('owner')), true)
  assert.equal(can_share_macro(macro('owner')), true)
  assert.equal(can_delete_macro(macro('edit', { id: 'u1' })), false)
  assert.equal(can_share_macro(macro('edit', { id: 'u1' })), false)
})
