import assert from 'node:assert/strict'
import test from 'node:test'
import { folder_mode, server_root } from '../src/lib/folder_mode.ts'

test('server_root trims the address and returns null when it is empty', () => {
  assert.equal(server_root(''), null)
  assert.equal(server_root('   '), null)
  assert.equal(server_root(' 10.0.77.11 '), String.raw`\\10.0.77.11`)
})

test('folder_mode recognizes server paths and treats other paths as drives', () => {
  assert.equal(folder_mode(String.raw`\\srv\a`), 'server')
  assert.equal(folder_mode(String.raw`D:\x`), 'drives')
  assert.equal(folder_mode(null), 'drives')
  assert.equal(folder_mode(undefined), 'drives')
})
