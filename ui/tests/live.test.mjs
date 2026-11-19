import assert from 'node:assert/strict'
import test from 'node:test'
import { affects, create_live_client, create_reload_gate, live_url, parse_live_message } from '../src/data/live.ts'

function create_fake_timers(first_handle = 0) {
  let next_handle = first_handle
  const callbacks = new Map()
  const cleared = []
  const scheduled = []
  return {
    timers: {
      set_timeout(fn, ms) {
        const handle = next_handle++
        callbacks.set(handle, { fn, ms })
        scheduled.push({ handle, ms })
        return handle
      },
      clear_timeout(handle) {
        cleared.push(handle)
        callbacks.delete(handle)
      },
    },
    fire(handle) {
      const timer = callbacks.get(handle)
      assert.ok(timer, `No timer for handle ${handle}`)
      callbacks.delete(handle)
      timer.fn()
    },
    pending: () => [...callbacks.keys()],
    scheduled,
    cleared,
  }
}

class FakeEventSource {
  constructor(url) {
    this.url = url
    this.readyState = 0
    this.onopen = null
    this.onmessage = null
    this.onerror = null
    this.close_count = 0
  }

  open() {
    this.readyState = 1
    this.onopen?.({})
  }

  send(data) {
    this.onmessage?.({ data })
  }

  fail(ready_state = 2) {
    this.readyState = ready_state
    this.onerror?.({})
  }

  close() {
    this.close_count += 1
    this.readyState = 2
  }
}

function create_client(timers = create_fake_timers(), reconnect_ms = 2000) {
  const sources = []
  const open = url => {
    const source = new FakeEventSource(url)
    sources.push(source)
    return source
  }
  const client = reconnect_ms === undefined
    ? create_live_client(open, timers.timers)
    : create_live_client(open, timers.timers, reconnect_ms)
  return { client, sources }
}

test('parse_live_message accepts string tables and rejects invalid payloads', () => {
  assert.deepEqual(parse_live_message('{"tables":["assets","jobs"]}'), new Set(['assets', 'jobs']))
  assert.equal(parse_live_message('{'), null)
  assert.equal(parse_live_message('{"other":[]}'), null)
  assert.equal(parse_live_message('{"tables":"assets"}'), null)
  assert.equal(parse_live_message('{"tables":["assets",2]}'), null)
  assert.equal(parse_live_message('null'), null)
})

test('affects matches listed tables and wildcard, but not unrelated tables', () => {
  assert.equal(affects(new Set(['assets']), ['assets', 'jobs']), true)
  assert.equal(affects(new Set(['jobs']), ['assets']), false)
  assert.equal(affects(new Set(['*']), ['assets']), true)
})

test('first subscription opens one source and additional subscriptions reuse it', () => {
  const { client, sources } = create_client()
  const unsubscribe_first = client.subscribe(['assets'], () => {})
  const unsubscribe_second = client.subscribe(['jobs'], () => {})
  assert.equal(sources.length, 1)
  assert.equal(sources[0].url, live_url)
  unsubscribe_first()
  assert.equal(sources[0].close_count, 0)
  unsubscribe_second()
  assert.equal(sources[0].close_count, 1)
})

test('messages notify only subscribers affected by the changed tables', () => {
  const { client, sources } = create_client()
  let assets_changes = 0
  let jobs_changes = 0
  client.subscribe(['assets'], () => { assets_changes += 1 })
  client.subscribe(['jobs'], () => { jobs_changes += 1 })
  sources[0].send('{"tables":["assets"]}')
  assert.equal(assets_changes, 1)
  assert.equal(jobs_changes, 0)
})

test('a throwing subscriber does not prevent other affected subscribers', () => {
  const { client, sources } = create_client()
  let later_changes = 0
  client.subscribe(['assets'], () => { throw new Error('subscriber failed') })
  client.subscribe(['assets'], () => { later_changes += 1 })
  assert.doesNotThrow(() => sources[0].send('{"tables":["assets"]}'))
  assert.equal(later_changes, 1)
})

test('closed EventSource errors reconnect after the configured timer', () => {
  const fake_timers = create_fake_timers(1)
  const { client, sources } = create_client(fake_timers, 1750)
  client.subscribe(['assets'], () => {})
  sources[0].fail(2)
  assert.equal(sources[0].close_count, 1)
  assert.deepEqual(fake_timers.pending(), [1])
  assert.deepEqual(fake_timers.scheduled.map(timer => timer.ms), [1750])
  fake_timers.fire(1)
  assert.equal(sources.length, 2)
})

test('initial open does not resync; open after an error notifies subscribers', () => {
  const fake_timers = create_fake_timers(1)
  const { client, sources } = create_client(fake_timers)
  let changes = 0
  client.subscribe(['assets'], () => { changes += 1 })
  sources[0].open()
  assert.equal(changes, 0)
  sources[0].fail(2)
  fake_timers.fire(1)
  sources[1].open()
  assert.equal(changes, 1)
  sources[1].open()
  assert.equal(changes, 1)
})

test('unsubscribing during reconnect cancels even a falsy timer handle', () => {
  const fake_timers = create_fake_timers(0)
  const { client, sources } = create_client(fake_timers)
  const unsubscribe = client.subscribe(['assets'], () => {})
  sources[0].fail(2)
  assert.deepEqual(fake_timers.pending(), [0])
  assert.deepEqual(fake_timers.scheduled.map(timer => timer.ms), [2000])
  unsubscribe()
  assert.deepEqual(fake_timers.cleared, [0])
  assert.deepEqual(fake_timers.pending(), [])
  assert.equal(sources[0].close_count, 1)
  assert.equal(sources.length, 1)
})

test('three gate requests before the timer runs produce one run', () => {
  const fake_timers = create_fake_timers()
  let runs = 0
  const gate = create_reload_gate(() => { runs += 1 }, 250, fake_timers.timers)
  gate.request()
  gate.request()
  gate.request()
  assert.deepEqual(fake_timers.pending(), [0])
  assert.deepEqual(fake_timers.scheduled.map(timer => timer.ms), [250])
  fake_timers.fire(0)
  assert.equal(runs, 1)
})

test('requests during an in-flight run coalesce into one follow-up', () => {
  const fake_timers = create_fake_timers(1)
  let runs = 0
  const gate = create_reload_gate(() => { runs += 1 }, 250, fake_timers.timers)
  gate.request()
  fake_timers.fire(1)
  assert.equal(runs, 1)
  gate.request()
  gate.request()
  gate.done()
  assert.deepEqual(fake_timers.pending(), [2])
  fake_timers.fire(2)
  assert.equal(runs, 2)
  gate.done()
})

test('dispose clears timer handle zero and prevents the run', () => {
  const fake_timers = create_fake_timers(0)
  let runs = 0
  const gate = create_reload_gate(() => { runs += 1 }, 250, fake_timers.timers)
  gate.request()
  gate.dispose()
  assert.deepEqual(fake_timers.cleared, [0])
  assert.deepEqual(fake_timers.pending(), [])
  gate.request()
  assert.equal(runs, 0)
})
