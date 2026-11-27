import assert from 'node:assert/strict'
import test from 'node:test'
import { access_label, access_levels, has_access, is_lock_conflict, owner_label, share_options } from '../src/lib/access.ts'

test('has_access checks every access level against every minimum', () => {
  const expected = [
    [true, false, false, false],
    [true, true, false, false],
    [true, true, true, false],
    [true, true, true, true],
  ]

  access_levels.forEach((access, access_index) => {
    access_levels.forEach((minimum, minimum_index) => {
      assert.equal(has_access(access, minimum), expected[access_index][minimum_index], `${access} >= ${minimum}`)
    })
  })
  assert.equal(has_access(null, 'view'), false)
  assert.equal(has_access(undefined, 'view'), false)
})

test('access_label returns the requested German labels', () => {
  assert.deepEqual(access_levels.map(access_label), ['Ansicht', 'Verwendung', 'Bearbeitung', 'Besitzer'])
})

test('share_options contains the requested choices in order', () => {
  assert.deepEqual(share_options, [
    { value: '', label: '–' },
    { value: 'view', label: 'Zur Ansicht' },
    { value: 'use', label: 'Zur Verwendung' },
    { value: 'edit', label: 'Zur Bearbeitung' },
  ])
})

test('owner_label prefers the display name, then email, and labels no owner Admin', () => {
  assert.equal(owner_label({ id: '1', display_name: 'Ada', email: 'ada@example.test' }), 'Ada')
  assert.equal(owner_label({ id: '2', display_name: '', email: 'grace@example.test' }), 'grace@example.test')
  assert.equal(owner_label(null), 'Admin')
})

test('is_lock_conflict recognizes both lock conflict details only', () => {
  assert.equal(is_lock_conflict('Wird gerade bearbeitet von Ada'), true)
  assert.equal(is_lock_conflict('Bearbeitungssperre fehlt oder wurde aufgehoben. Bitte neu laden.'), true)
  assert.equal(is_lock_conflict('Anderer Fehler'), false)
})
