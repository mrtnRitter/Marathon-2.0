import assert from 'node:assert/strict'
import test from 'node:test'
import { filter_assets } from '../src/lib/asset_filter.ts'

const assets = [
  { name: 'Clip A', status: 'pending' },
  { name: 'clip b', status: 'done' },
  { name: 'Other', status: 'failed' },
]

test('filter_assets includes all assets for the all status', () => {
  assert.deepEqual(filter_assets(assets, 'all', ''), assets)
})

test('filter_assets filters by status', () => {
  assert.deepEqual(filter_assets(assets, 'done', ''), [assets[1]])
})

test('filter_assets trims and matches search case-insensitively', () => {
  assert.deepEqual(filter_assets(assets, 'all', ' CLIP '), [assets[0], assets[1]])
})

test('filter_assets returns no status-mismatched search results', () => {
  assert.deepEqual(filter_assets(assets, 'pending', 'other'), [])
})
