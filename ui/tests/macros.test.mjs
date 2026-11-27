import test from 'node:test'
import assert from 'node:assert/strict'
import { macro_owner_label, macro_usage_label, can_delete_macro } from '../src/lib/macros.ts'

test('macro_owner_label handles standard, display name, and email fallbacks', () => {
  assert.equal(macro_owner_label(null), 'Standard')
  assert.equal(macro_owner_label({ id: 'u1', display_name: 'Alex', email: 'alex@example.test' }), 'Alex')
  assert.equal(macro_owner_label({ id: 'u1', display_name: '', email: 'alex@example.test' }), 'alex@example.test')
})

test('macro_usage_label uses the required singular and plural labels', () => {
  assert.equal(macro_usage_label(0), '–')
  assert.equal(macro_usage_label(1), '1 Workflow')
  assert.equal(macro_usage_label(4), '4 Workflows')
})

test('can_delete_macro requires edit access and a non-standard owner', () => {
  assert.equal(can_delete_macro({ can_edit: true, owner: { id: 'u1', display_name: 'Alex', email: 'alex@example.test' } }), true)
  assert.equal(can_delete_macro({ can_edit: false, owner: { id: 'u1', display_name: 'Alex', email: 'alex@example.test' } }), false)
  assert.equal(can_delete_macro({ can_edit: true, owner: null }), false)
})
