import assert from 'node:assert/strict'
import test from 'node:test'
import { new_module, to_nodes } from '../src/features/workflow-editor/graph_mapping.ts'

const masked_hull = {
  id: '', version: 3, sort_order: 0, name: '', category: '', icon: '', description: '',
  has_input: true, outputs: [{ id: 'out', label: 'Ausgang', description: '' }], settings: [],
}

const base_module = {
  id: 'instance-1', type_id: '', name: '', description: '', position: { x: 0, y: 0 },
  settings: {}, counters: { waiting: 1, running: 0, done: 2, failed: 0, discovered: 0 },
  locked: false, macro_version: 2, hull: masked_hull, access: null, blind: true,
}

test('to_nodes uses the blind module hull without searching the palette', () => {
  const graph = { workflow_id: 'workflow-1', version: 1, modules: [base_module], connections: [] }
  const palette = new Proxy([], {
    get(target, property, receiver) {
      if (property === 'find') throw new Error('blind module must not search the palette')
      return Reflect.get(target, property, receiver)
    },
  })

  const node = to_nodes(graph, palette)[0]

  assert.equal(node.data.module_type, masked_hull)
  assert.equal(node.data.module_type.name, '')
  assert.equal(node.data.module_type.outputs[0].label, 'Ausgang')
})

test('to_nodes preserves access for a visible module', () => {
  const visible_hull = { ...masked_hull, id: 'macro-1', name: 'Visible' }
  const visible_module = { ...base_module, type_id: visible_hull.id, name: 'Visible', hull: visible_hull, access: 'view', blind: false }
  const graph = { workflow_id: 'workflow-1', version: 1, modules: [visible_module], connections: [] }
  const palette_entry = { ...visible_hull, owner: null, access: 'edit', usage: 0, updated_at: '' }

  const node = to_nodes(graph, [palette_entry])[0]

  assert.equal(node.data.module.access, 'view')
})

test('new_module copies access and marks the new module non-blind', () => {
  const macro = {
    id: 'macro-1', version: 4, sort_order: 0, name: 'Visible', category: 'Tools', icon: 'workflow',
    description: '', has_input: true, outputs: [], settings: [], owner: null, access: 'edit',
    usage: 0, updated_at: '',
  }

  const module = new_module(macro, { x: 10, y: 20 }, 0)

  assert.equal(module.access, 'edit')
  assert.equal(module.blind, false)
})
