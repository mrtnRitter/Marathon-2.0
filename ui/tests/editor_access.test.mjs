import assert from 'node:assert/strict'
import test from 'node:test'
import { filter_usable_module_types } from '../src/features/workflow-editor/editor_access.ts'
import { has_access } from '../src/lib/access.ts'

const modules = [
  { id: 'view', access: 'view' },
  { id: 'use', access: 'use' },
  { id: 'edit', access: 'edit' },
  { id: 'owner', access: 'owner' },
]

test('palette filtering includes only modules with use access or higher', () => {
  assert.deepEqual(filter_usable_module_types(modules, has_access).map(module => module.id), ['use', 'edit', 'owner'])
  assert.deepEqual(modules.map(module => module.id), ['view', 'use', 'edit', 'owner'])
})

test('palette filtering rejects missing access', () => {
  assert.deepEqual(filter_usable_module_types([{ id: 'none', access: null }, { id: 'unknown' }], has_access), [])
})
