import assert from 'node:assert/strict'
import test from 'node:test'
import { ignore_select_changes } from '../src/features/workflow-editor/selection.ts'

test('ignore_select_changes only ignores selection changes for a non-empty rectangle', () => {
  assert.equal(ignore_select_changes(null), false)
  assert.equal(ignore_select_changes({ width: 0, height: 0 }), false)
  assert.equal(ignore_select_changes({ width: 10, height: 0 }), true)
  assert.equal(ignore_select_changes({ width: 10, height: 10 }), true)
})
