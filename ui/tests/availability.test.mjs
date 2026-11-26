import assert from 'node:assert/strict'
import test from 'node:test'
import { format_time_window, has_availability_rules, parse_time_window, programs_from_lines } from '../src/lib/availability.ts'

const empty_config = () => ({
  schedule: {
    enabled: false,
    days: Object.fromEntries(['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'].map(day => [day, []])),
  },
  idle: { enabled: false, minutes: 30, max_cpu_percent: 50, max_ram_percent: 70, max_gpu_percent: 80 },
  blocked_processes: [],
})

test('parses fixed HH:MM windows, including midnight and overnight ranges', () => {
  assert.deepEqual(parse_time_window('08:15', '17:45'), { start: '08:15', end: '17:45' })
  assert.deepEqual(parse_time_window('19:00', '07:00'), { start: '19:00', end: '07:00' })
  assert.deepEqual(parse_time_window('00:00', '24:00'), { start: '00:00', end: '24:00' })
})

test('rejects malformed times and 24:00 as a start', () => {
  for (const [start, end] of [['8:00', '09:00'], ['24:00', '01:00'], ['23:59', '24:01'], ['12:60', '13:00'], ['', '09:00']]) {
    assert.equal(parse_time_window(start, end), null, `${start}-${end}`)
  }
})

test('formats a parsed time window for display', () => {
  assert.equal(format_time_window({ start: '19:00', end: '07:00' }), '19:00–07:00')
})

test('converts one trimmed program name per non-empty line', () => {
  assert.deepEqual(programs_from_lines(' Resolve.exe \n\n blender.exe\n Resolve.exe '), ['Resolve.exe', 'blender.exe', 'Resolve.exe'])
})

test('detects whether schedule, programs, or idle activates a rule', () => {
  const config = empty_config()
  assert.equal(has_availability_rules(config), false)
  assert.equal(has_availability_rules({ ...config, schedule: { ...config.schedule, enabled: true } }), true)
  assert.equal(has_availability_rules({ ...config, blocked_processes: ['Resolve.exe'] }), true)
  assert.equal(has_availability_rules({ ...config, idle: { ...config.idle, enabled: true } }), true)
})
