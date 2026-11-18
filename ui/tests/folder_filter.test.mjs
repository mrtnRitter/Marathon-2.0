import assert from 'node:assert/strict'
import test from 'node:test'
import { filter_dirs } from '../src/lib/folder_filter.ts'

const dirs = [
  { path: 'D:\\Ingest\\Projekt' },
  { path: 'D:\\Ingest\\proxy' },
  { path: 'D:\\Ingest\\Archiv' },
]

const listing_path = 'D:\\Ingest'

test('filter_dirs keeps all entries while typing the current listing path', () => {
  assert.deepEqual(filter_dirs(dirs, listing_path, 'D:\\Ingest'), dirs)
  assert.deepEqual(filter_dirs(dirs, listing_path, 'D:\\Ingest\\'), dirs)
  assert.deepEqual(filter_dirs(dirs, listing_path, 'E:\\other'), dirs)
  assert.deepEqual(filter_dirs(dirs, listing_path, ''), dirs)
})

test('filter_dirs matches directory paths case-insensitively', () => {
  assert.deepEqual(filter_dirs(dirs, listing_path, 'd:\\ingest\\PRO'), [dirs[0], dirs[1]])
  assert.deepEqual(filter_dirs(dirs, listing_path, 'D:\\Ingest\\x'), [])
  assert.deepEqual(filter_dirs(dirs, listing_path, 'D:/Ingest/Ar'), [dirs[2]])
})

test('filter_dirs filters drive roots while listing drives', () => {
  const drives = [{ path: 'C:\\' }, { path: 'D:\\' }]
  assert.deepEqual(filter_dirs(drives, null, 'd'), [drives[1]])
})

test('filter_dirs supports Linux paths', () => {
  const linux_dirs = [{ path: '/mnt/media' }, { path: '/mnt/backup' }]
  assert.deepEqual(filter_dirs(linux_dirs, '/mnt', '/mnt/me'), [linux_dirs[0]])
})

test('filter_dirs supports UNC shares', () => {
  const share_dirs = [
    { path: '\\\\10.0.77.11\\Media' },
    { path: '\\\\10.0.77.11\\Backup' },
  ]
  assert.deepEqual(filter_dirs(share_dirs, '\\\\10.0.77.11', '\\\\10.0.77.11\\m'), [share_dirs[0]])
})
