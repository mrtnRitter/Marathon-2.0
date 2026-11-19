import assert from 'node:assert/strict'
import test from 'node:test'
import { add_skill, available_skills, move_skill, remove_skill, shown_skills } from '../src/lib/skills.ts'

test('move_skill swaps an entry in either direction without mutating the source', () => {
  const skills = ['copy_file', 'move_file', 'rename_file']
  assert.deepEqual(move_skill(skills, 1, -1), ['move_file', 'copy_file', 'rename_file'])
  assert.deepEqual(move_skill(skills, 1, 1), ['copy_file', 'rename_file', 'move_file'])
  assert.deepEqual(skills, ['copy_file', 'move_file', 'rename_file'])
})

test('move_skill leaves first and last entries in place at list boundaries', () => {
  const skills = ['copy_file', 'move_file']
  assert.deepEqual(move_skill(skills, 0, -1), skills)
  assert.deepEqual(move_skill(skills, 1, 1), skills)
})

test('remove_skill removes entries, including the first or last', () => {
  const skills = ['copy_file', 'move_file', 'rename_file']
  assert.deepEqual(remove_skill(skills, 1), ['copy_file', 'rename_file'])
  assert.deepEqual(remove_skill(skills, 0), ['move_file', 'rename_file'])
  assert.deepEqual(remove_skill(skills, 2), ['copy_file', 'move_file'])
  assert.deepEqual(skills, ['copy_file', 'move_file', 'rename_file'])
})

test('add_skill appends a new skill and does not add duplicates', () => {
  const skills = ['copy_file']
  assert.deepEqual(add_skill(skills, 'move_file'), ['copy_file', 'move_file'])
  assert.deepEqual(add_skill(skills, 'copy_file'), ['copy_file'])
  assert.deepEqual(skills, ['copy_file'])
})

test('available_skills filters listed skills and preserves capability order', () => {
  assert.deepEqual(available_skills(['move_file', 'copy_file', 'rename_file'], ['copy_file']), ['move_file', 'rename_file'])
  assert.deepEqual(available_skills(['copy_file'], ['copy_file']), [])
})

test('shown_skills filters unsupported skills and preserves priority order', () => {
  assert.deepEqual(shown_skills(['move_file', 'copy_file', 'unknown'], ['copy_file', 'move_file']), ['move_file', 'copy_file'])
  assert.deepEqual(shown_skills([], ['copy_file']), [])
})
