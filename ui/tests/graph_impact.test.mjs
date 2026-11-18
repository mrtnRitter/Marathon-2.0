import assert from 'node:assert/strict'
import test from 'node:test'
import { impact_lines } from '../src/lib/graph_impact.ts'

test('impact_lines describes the affected workflow changes in order', () => {
  assert.deepEqual(impact_lines({
    removed_modules: [
      { id: 'm', name: 'Kopieren', assets: 3, jobs: 1 },
      { id: 'n', name: 'QC', assets: 1, jobs: 0 },
    ],
    assets: 4,
    jobs: 1,
    outcomes: { rerouted: 2, done: 1, failed: 1 },
  }), [
    'Änderung betrifft laufende Assets.',
    '2 Assets werden ab dem vorherigen Modul neu verteilt',
    '1 Asset wird als erledigt abgeschlossen',
    '1 Asset wird als fehlgeschlagen markiert',
    '1 Job wird abgebrochen',
    'Betroffene Module: Kopieren, QC',
  ])
})

test('impact_lines omits zero outcomes and uses plural wording', () => {
  assert.deepEqual(impact_lines({
    removed_modules: [{ id: 'm', name: 'Kopieren', assets: 3, jobs: 2 }],
    assets: 3,
    jobs: 2,
    outcomes: { rerouted: 0, done: 0, failed: 3 },
  }), [
    'Änderung betrifft laufende Assets.',
    '3 Assets werden als fehlgeschlagen markiert',
    '2 Jobs werden abgebrochen',
    'Betroffene Module: Kopieren',
  ])
})
