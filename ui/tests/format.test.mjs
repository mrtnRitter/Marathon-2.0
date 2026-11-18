import assert from 'node:assert/strict'
import test from 'node:test'
import { format_datetime, format_metadata, format_size } from '../src/lib/format.ts'

test('format_size uses binary units and German number formatting', () => {
  assert.equal(format_size(0), '0 B')
  assert.equal(format_size(1023), '1023 B')
  assert.equal(format_size(1536), '1,5 KB')
  assert.equal(format_size(1048576), '1 MB')
  assert.equal(format_size(5 * 1024 ** 3), '5 GB')
  assert.equal(format_size(2.25 * 1024 ** 4), '2,3 TB')
  assert.equal(format_size(3 * 1024 ** 5), '3072 TB')
})

test('format_metadata orders known fields and formats supported values', () => {
  const changed_at = '2026-11-17T10:00:00+00:00'
  assert.deepEqual(format_metadata({
    mtime: changed_at,
    note_text: 'x',
    size: '1536',
    source_path: 'D:\\in\\a.mxf',
  }), [
    ['Quellpfad', 'D:\\in\\a.mxf'],
    ['Größe', '1,5 KB'],
    ['Datei geändert', format_datetime(changed_at)],
    ['Note text', 'x'],
  ])
  assert.deepEqual(format_metadata({ size: 'abc' }), [['Größe', 'abc']])
})
