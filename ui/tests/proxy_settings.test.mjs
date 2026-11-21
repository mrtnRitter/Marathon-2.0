import assert from 'node:assert/strict'
import test from 'node:test'
import { format_proxy_addresses, parse_proxy_addresses } from '../src/lib/proxy_settings.ts'

test('parse_proxy_addresses splits, trims, and removes empty entries', () => {
  assert.deepEqual(parse_proxy_addresses(' 10.0.0.1, ,::1 ,'), ['10.0.0.1', '::1'])
  assert.deepEqual(parse_proxy_addresses(''), [])
})

test('format_proxy_addresses joins addresses with a comma and space', () => {
  assert.equal(format_proxy_addresses(['10.0.0.1', '::1']), '10.0.0.1, ::1')
  assert.equal(format_proxy_addresses([]), '')
})
