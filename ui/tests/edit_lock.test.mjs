import assert from 'node:assert/strict'
import test from 'node:test'
import { create_edit_lock } from '../src/lib/edit_lock.ts'

const grant = (token = 'token-1', heartbeat_s = 5) => ({ token, expires_at: '2030-01-01T00:00:00Z', heartbeat_s })
const error = (message, status) => Object.assign(new Error(message), status === undefined ? {} : { status })

class FakeTimers {
  now = 0
  next_id = 1
  jobs = new Map()
  set_timeout(callback, ms) {
    const id = this.next_id++
    this.jobs.set(id, { callback, due: this.now + ms })
    return id
  }
  clear_timeout(id) { this.jobs.delete(id) }
  pending() { return this.jobs.size }
  advance(ms) {
    const target = this.now + ms
    while (true) {
      const next = [...this.jobs.entries()].sort((a, b) => a[1].due - b[1].due || a[0] - b[0])[0]
      if (!next || next[1].due > target) break
      this.now = next[1].due
      this.jobs.delete(next[0])
      next[1].callback()
    }
    this.now = target
  }
}

class FakeApi {
  calls = []
  throw_next_acquire = false
  reject_release = false
  acquire(token) {
    const call = { kind: 'acquire', token, settled: false }
    this.calls.push(call)
    if (this.throw_next_acquire) {
      this.throw_next_acquire = false
      call.settled = true
      throw error('synchronous failure')
    }
    call.promise = new Promise((resolve, reject) => {
      call.resolve = value => { call.settled = true; resolve(value) }
      call.reject = reason => { call.settled = true; reject(reason) }
    })
    return call.promise
  }
  release(token) {
    this.calls.push({ kind: 'release', token, settled: true })
    return this.reject_release ? Promise.reject(error('release failed')) : Promise.resolve()
  }
  acquire_calls() { return this.calls.filter(call => call.kind === 'acquire') }
  release_calls() { return this.calls.filter(call => call.kind === 'release') }
  settle_pending() {
    this.acquire_calls().forEach((call, index) => {
      if (!call.settled) call.resolve(grant('late-' + index, 6))
    })
  }
}

const flush = async () => {
  await Promise.resolve()
  await new Promise(resolve => setImmediate(resolve))
  await Promise.resolve()
}

async function scenario(run) {
  const timers = new FakeTimers()
  const api = new FakeApi()
  const changes = []
  const unhandled = []
  const listener = reason => unhandled.push(reason)
  process.on('unhandledRejection', listener)
  const controller = create_edit_lock(api, state => changes.push(state), { timers, request_timeout_ms: 10000, max_failures: 3 })
  let failure
  try { await run({ api, timers, controller, changes }) } catch (caught) { failure = caught }
  finally {
    controller.stop()
    api.settle_pending()
    await flush()
    process.off('unhandledRejection', listener)
  }
  if (failure) throw failure
  assert.deepEqual(unhandled, [], 'must not emit unhandled rejections')
  assert.equal(timers.pending(), 0, 'scenario cleanup leaves no timers')
}

test('acquire grants the lock and heartbeats with its token', async () => scenario(async ({ api, timers, controller }) => {
  controller.start()
  assert.equal(controller.state().status, 'acquiring')
  assert.equal(api.acquire_calls().length, 1)
  api.acquire_calls()[0].resolve(grant('held-token', 5))
  await flush()
  assert.deepEqual(controller.state(), { status: 'held', token: 'held-token', expires_at: grant().expires_at, message: null, conflict: false })
  assert.equal(timers.pending(), 1)
  timers.advance(4999)
  assert.equal(api.acquire_calls().length, 1)
  timers.advance(1)
  assert.equal(api.acquire_calls().length, 2)
  assert.equal(api.acquire_calls()[1].token, 'held-token')
  api.acquire_calls()[1].resolve(grant('held-token-2', 7))
  await flush()
  assert.equal(controller.state().token, 'held-token-2')
  assert.equal(timers.pending(), 1)
}))

test('first request 409 is denied with conflict', async () => scenario(async ({ api, controller, timers }) => {
  controller.start()
  api.acquire_calls()[0].reject(error('Wird gerade bearbeitet', 409))
  await flush()
  assert.deepEqual(controller.state(), { status: 'denied', token: null, expires_at: null, message: 'Wird gerade bearbeitet', conflict: true })
  assert.equal(timers.pending(), 0)
}))

test('first request non-conflict error is denied', async () => scenario(async ({ api, controller, timers }) => {
  controller.start()
  api.acquire_calls()[0].reject(error('offline', 503))
  await flush()
  assert.equal(controller.state().status, 'denied')
  assert.equal(controller.state().message, 'offline')
  assert.equal(controller.state().conflict, false)
  assert.equal(timers.pending(), 0)
}))

test('first request timeout denies, then a late grant is adopted', async () => scenario(async ({ api, timers, controller }) => {
  controller.start()
  timers.advance(10000)
  assert.equal(controller.state().status, 'denied')
  assert.equal(controller.state().message, 'Server nicht erreichbar')
  assert.equal(controller.state().conflict, false)
  assert.equal(timers.pending(), 0)
  api.acquire_calls()[0].resolve(grant('late-first'))
  await flush()
  assert.equal(controller.state().status, 'held')
  assert.equal(controller.state().token, 'late-first')
  assert.equal(timers.pending(), 1)
}))

test('heartbeat 409 loses the lock and discards its token', async () => scenario(async ({ api, timers, controller }) => {
  controller.start()
  api.acquire_calls()[0].resolve(grant('first', 5))
  await flush()
  timers.advance(5000)
  api.acquire_calls()[1].reject(error('conflict', 409))
  await flush()
  assert.deepEqual(controller.state(), { status: 'lost', token: null, expires_at: null, message: 'conflict', conflict: true })
  assert.equal(timers.pending(), 0)
  controller.start()
  assert.equal(api.acquire_calls()[2].token, null)
}))

test('three heartbeat failures lose the lock while retaining the token for retry', async () => scenario(async ({ api, timers, controller }) => {
  controller.start()
  api.acquire_calls()[0].resolve(grant('keep-me', 5))
  await flush()
  for (let index = 1; index <= 3; index += 1) {
    timers.advance(5000)
    api.acquire_calls()[index].reject(error('failure-' + index, 503))
    await flush()
    if (index < 3) {
      assert.equal(controller.state().status, 'held')
      assert.equal(controller.state().token, 'keep-me')
    }
  }
  assert.equal(controller.state().status, 'lost')
  assert.equal(controller.state().conflict, false)
  assert.equal(controller.state().message, 'failure-3')
  assert.equal(timers.pending(), 0)
  controller.start()
  assert.equal(api.acquire_calls()[4].token, 'keep-me')
}))

test('a successful heartbeat resets consecutive failures', async () => scenario(async ({ api, timers, controller }) => {
  controller.start()
  api.acquire_calls()[0].resolve(grant('same', 5))
  await flush()
  for (let index = 1; index <= 2; index += 1) {
    timers.advance(5000)
    api.acquire_calls()[index].reject(error('temporary', 503))
    await flush()
    assert.equal(controller.state().status, 'held')
  }
  timers.advance(5000)
  api.acquire_calls()[3].resolve(grant('same', 5))
  await flush()
  timers.advance(5000)
  api.acquire_calls()[4].reject(error('temporary', 503))
  await flush()
  timers.advance(5000)
  api.acquire_calls()[5].reject(error('temporary', 503))
  await flush()
  assert.equal(controller.state().status, 'held')
  assert.equal(timers.pending(), 1)
}))

test('heartbeat timeout counts as failure and late grant cancels retry', async () => scenario(async ({ api, timers, controller }) => {
  controller.start()
  api.acquire_calls()[0].resolve(grant('heartbeat-token', 5))
  await flush()
  timers.advance(5000)
  timers.advance(10000)
  assert.equal(controller.state().status, 'held')
  assert.equal(timers.pending(), 1)
  api.acquire_calls()[1].resolve(grant('late-heartbeat', 8))
  await flush()
  assert.equal(controller.state().status, 'held')
  assert.equal(controller.state().token, 'late-heartbeat')
  assert.equal(timers.pending(), 1)
  timers.advance(8000)
  assert.equal(api.acquire_calls().length, 3)
}))

test('stop during acquire is immediate and releases a late grant', async () => scenario(async ({ api, timers, controller }) => {
  controller.start()
  controller.stop()
  assert.equal(controller.state().status, 'idle')
  assert.equal(timers.pending(), 0)
  api.acquire_calls()[0].resolve(grant('after-stop'))
  await flush()
  assert.equal(controller.state().status, 'idle')
  assert.deepEqual(api.release_calls().map(call => call.token), ['after-stop'])
}))

test('stop while held releases exactly once and clears timers', async () => scenario(async ({ api, timers, controller }) => {
  controller.start()
  api.acquire_calls()[0].resolve(grant('release-once'))
  await flush()
  controller.stop()
  await flush()
  assert.equal(controller.state().status, 'idle')
  assert.deepEqual(api.release_calls().map(call => call.token), ['release-once'])
  assert.equal(timers.pending(), 0)
  controller.stop()
  assert.equal(api.release_calls().length, 1)
}))

test('rejected release is swallowed', async () => scenario(async ({ api, controller }) => {
  api.reject_release = true
  controller.start()
  api.acquire_calls()[0].resolve(grant('rejecting-release'))
  await flush()
  controller.stop()
  await flush()
  assert.equal(api.release_calls().length, 1)
}))

test('starting twice while acquiring sends one request', async () => scenario(async ({ api, controller }) => {
  controller.start()
  controller.start()
  assert.equal(api.acquire_calls().length, 1)
}))

test('retry from lost reuses token without conflict and drops it after conflict', async () => scenario(async ({ api, timers, controller }) => {
  controller.start()
  api.acquire_calls()[0].resolve(grant('reusable', 5))
  await flush()
  for (let index = 1; index <= 3; index += 1) {
    timers.advance(5000)
    api.acquire_calls()[index].reject(error('offline', 503))
    await flush()
  }
  controller.start()
  assert.equal(api.acquire_calls()[4].token, 'reusable')
  api.acquire_calls()[4].reject(error('conflict', 409))
  await flush()
  controller.start()
  assert.equal(api.acquire_calls()[5].token, null)
}))

test('synchronously throwing acquire becomes denied', async () => scenario(async ({ api, controller, timers }) => {
  api.throw_next_acquire = true
  controller.start()
  assert.equal(controller.state().status, 'denied')
  assert.equal(controller.state().message, 'synchronous failure')
  assert.equal(timers.pending(), 0)
}))

test('non-Error failures use the unavailable message', async () => scenario(async ({ api, controller }) => {
  controller.start()
  api.acquire_calls()[0].reject('not an Error')
  await flush()
  assert.equal(controller.state().message, 'Server nicht erreichbar')
}))


test('late heartbeat result is ignored after a newer request has started', async () => scenario(async ({ api, timers, controller }) => {
  controller.start()
  api.acquire_calls()[0].resolve(grant('current', 5))
  await flush()
  timers.advance(5000)
  timers.advance(10000)
  timers.advance(5000)
  assert.equal(api.acquire_calls().length, 3)
  api.acquire_calls()[1].resolve(grant('stale', 5))
  await flush()
  assert.equal(controller.state().status, 'held')
  assert.equal(controller.state().token, 'current')
  assert.equal(timers.pending(), 1)
  api.acquire_calls()[2].resolve(grant('newer', 5))
  await flush()
  assert.equal(controller.state().token, 'newer')
}))


test('stop after a failed retry releases the token remembered from lost', async () => scenario(async ({ api, timers, controller }) => {
  controller.start()
  api.acquire_calls()[0].resolve(grant('remembered', 5))
  await flush()
  for (let index = 1; index <= 3; index += 1) {
    timers.advance(5000)
    api.acquire_calls()[index].reject(error('offline', 503))
    await flush()
  }
  controller.start()
  api.acquire_calls()[4].reject(error('offline again', 503))
  await flush()
  assert.equal(controller.state().status, 'denied')
  controller.stop()
  await flush()
  assert.deepEqual(api.release_calls().map(call => call.token), ['remembered'])
}))

test('lock_holder_name extracts a trimmed holder after the lock prefix', async () => {
  const { lock_holder_name } = await import('../src/lib/edit_lock.ts')
  assert.equal(lock_holder_name('Wird gerade bearbeitet von Anna'), 'Anna')
  assert.equal(lock_holder_name('Wird gerade bearbeitet von  Anna  '), 'Anna')
  assert.equal(lock_holder_name(null), null)
  assert.equal(lock_holder_name('anderer Text'), null)
  assert.equal(lock_holder_name('Wird gerade bearbeitet von '), null)
})
