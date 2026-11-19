import assert from 'node:assert/strict'
import test from 'node:test'
import * as live from '../src/data/live.ts'

const { affects, create_live_client, create_reload_gate, live_url, parse_live_message } = live

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
  assert.deepEqual(fake_timers.pending(), [1, 2])
  assert.deepEqual(fake_timers.scheduled.map(timer => timer.ms), [2000, 1750])
  fake_timers.fire(2)
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
  fake_timers.fire(3)
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
  assert.deepEqual(fake_timers.pending(), [0, 1])
  assert.deepEqual(fake_timers.scheduled.map(timer => timer.ms), [2000, 2000])
  unsubscribe()
  assert.deepEqual(fake_timers.cleared, [1, 0])
  assert.deepEqual(fake_timers.pending(), [])
  assert.equal(sources[0].close_count, 1)
  assert.equal(sources.length, 1)
})


test('connection subscriber opens the source and times out after 2000 ms', () => {
  const fake_timers = create_fake_timers()
  const { client, sources } = create_client(fake_timers)
  const states = []
  const unsubscribe = client.subscribe_connection(connected => { states.push(connected) })
  assert.equal(live.offline_after_ms, 2000)
  assert.equal(client.is_connected(), true)
  assert.equal(sources.length, 1)
  assert.deepEqual(fake_timers.scheduled.map(timer => timer.ms), [2000])
  fake_timers.fire(fake_timers.pending()[0])
  assert.deepEqual(states, [false])
  assert.equal(client.is_connected(), false)
  sources[0].open()
  sources[0].open()
  assert.deepEqual(states, [false, true])
  unsubscribe()
})

test('error followed by open before the deadline keeps connection subscribers quiet', () => {
  const fake_timers = create_fake_timers()
  const { client, sources } = create_client(fake_timers)
  const states = []
  const unsubscribe = client.subscribe_connection(connected => { states.push(connected) })
  sources[0].open()
  sources[0].fail(0)
  assert.deepEqual(fake_timers.scheduled.map(timer => timer.ms), [2000, 2000])
  sources[0].open()
  assert.deepEqual(fake_timers.pending(), [])
  assert.deepEqual(states, [])
  unsubscribe()
})

test('readyState zero error becomes offline if no open arrives before timeout', () => {
  const fake_timers = create_fake_timers()
  const { client, sources } = create_client(fake_timers)
  const states = []
  const unsubscribe = client.subscribe_connection(connected => { states.push(connected) })
  sources[0].open()
  sources[0].fail(0)
  assert.deepEqual(fake_timers.scheduled.map(timer => timer.ms), [2000, 2000])
  fake_timers.fire(fake_timers.pending()[0])
  assert.deepEqual(states, [false])
  assert.equal(sources.length, 1)
  unsubscribe()
})

test('duplicate callback subscriptions notify and unsubscribe independently', () => {
  const fake_timers = create_fake_timers()
  const { client, sources } = create_client(fake_timers)
  let changes = 0
  const listener = () => { changes += 1 }
  const unsubscribe_first = client.subscribe_connection(listener)
  const unsubscribe_second = client.subscribe_connection(listener)
  sources[0].open()
  sources[0].fail(0)
  fake_timers.fire(fake_timers.pending()[0])
  assert.equal(changes, 2)
  unsubscribe_first()
  assert.equal(sources[0].close_count, 0)
  unsubscribe_second()
  assert.equal(sources[0].close_count, 1)
})

test('closed-source reconnect reports recovery once after going offline', () => {
  const fake_timers = create_fake_timers()
  const { client, sources } = create_client(fake_timers)
  const states = []
  const unsubscribe = client.subscribe_connection(connected => { states.push(connected) })
  fake_timers.fire(fake_timers.pending()[0])
  assert.deepEqual(states, [false])
  sources[0].fail(2)
  assert.equal(fake_timers.pending().length, 2)
  const reconnect = fake_timers.scheduled.at(-1)
  fake_timers.fire(reconnect.handle)
  assert.equal(sources.length, 2)
  sources[1].open()
  assert.deepEqual(states, [false, true])
  unsubscribe()
})

test('last connection unsubscribe clears timeout handle zero', () => {
  const fake_timers = create_fake_timers(0)
  const { client, sources } = create_client(fake_timers)
  const unsubscribe = client.subscribe_connection(() => {})
  assert.deepEqual(fake_timers.pending(), [0])
  unsubscribe()
  assert.deepEqual(fake_timers.cleared, [0])
  assert.deepEqual(fake_timers.pending(), [])
  assert.equal(sources[0].close_count, 1)
})

test('a throwing connection subscriber does not prevent other notifications', () => {
  const fake_timers = create_fake_timers()
  const { client, sources } = create_client(fake_timers)
  let later_changes = 0
  const unsubscribe_first = client.subscribe_connection(() => { throw new Error('subscriber failed') })
  const unsubscribe_second = client.subscribe_connection(() => { later_changes += 1 })
  sources[0].open()
  sources[0].fail(0)
  assert.doesNotThrow(() => fake_timers.fire(fake_timers.pending()[0]))
  assert.equal(later_changes, 1)
  unsubscribe_first()
  unsubscribe_second()
})

test('strict effect subscribe-cleanup-resubscribe leaves one connection subscription active', () => {
  const fake_timers = create_fake_timers()
  const { client, sources } = create_client(fake_timers)
  const notifications = []
  const strict_cleanup = client.subscribe_connection(() => { notifications.push('cleaned') })
  strict_cleanup()
  const unmount = client.subscribe_connection(() => { notifications.push('active') })
  assert.equal(sources.length, 2)
  assert.equal(sources[0].close_count, 1)
  fake_timers.fire(fake_timers.pending()[0])
  assert.deepEqual(notifications, ['active'])
  unmount()
  assert.equal(sources[1].close_count, 1)
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
