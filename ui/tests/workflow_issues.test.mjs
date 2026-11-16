import assert from 'node:assert/strict'
import test from 'node:test'
import { blocked_message, issue_title, split_startable } from '../src/lib/workflow_issues.ts'

test('split_startable separates inactive workflows with issues', () => {
  const workflows = [
    { name: 'A', active: false, issues: ['W'] },
    { name: 'B', active: false, issues: [] },
    { name: 'C', active: true, issues: ['X'] },
  ]
  const result = split_startable(workflows)
  assert.deepEqual(result.blocked, [workflows[0]])
  assert.deepEqual(result.startable, [workflows[1], workflows[2]])
})

test('blocked_message describes no, one, or multiple blocked workflows', () => {
  assert.equal(blocked_message([]), null)
  assert.equal(blocked_message([{ name: 'A' }]), '„A“ kann nicht gestartet werden: Einstellungen prüfen')
  assert.equal(blocked_message([{ name: 'A' }, { name: 'B' }]), '2 Workflows können nicht gestartet werden: Einstellungen prüfen')
})

test('issue_title lists modules that need settings checked', () => {
  assert.equal(issue_title(['W', 'X']), 'Einstellungen prüfen: W, X')
})
