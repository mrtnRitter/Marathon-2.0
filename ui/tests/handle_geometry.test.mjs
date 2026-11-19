import assert from 'node:assert/strict'
import test from 'node:test'
import { connection_points, saved_edge_points } from '../src/features/workflow-editor/handle_geometry.ts'

test('saved edge begins at the lower outside rim and keeps the normal input top rim', () => {
  const points = saved_edge_points(
    { x: 120, y: 40 },
    { x: 300, y: 25 },
  )

  assert.deepEqual(points, {
    source: { x: 120, y: 45 },
    target: { x: 300, y: 25 },
  })
})

test('drag line converts the measured source-area center and normal target-area center', () => {
  const points = connection_points(
    { x: 120, y: 40 },
    { type: 'source', width: 80, height: 24 },
    { x: 300, y: 25 },
    { type: 'target', width: 10, height: 10 },
  )

  assert.deepEqual(points, {
    source: { x: 120, y: 57 },
    target: { x: 300, y: 20 },
  })
})

test('drag line uses the top of the measured expanded target area', () => {
  const points = connection_points(
    { x: 120, y: 40 },
    { type: 'source', width: 80, height: 24 },
    { x: 300, y: 75 },
    { type: 'target', width: 240, height: 150 },
  )

  assert.deepEqual(points.target, { x: 300, y: 0 })
})


test('input reconnection starts at the top rim of its measured expanded handle area', () => {
  const points = connection_points(
    { x: 300, y: 75 },
    { type: 'target', width: 240, height: 150 },
    { x: 415, y: 278 },
    null,
  )

  assert.deepEqual(points.source, { x: 300, y: 0 })
  assert.deepEqual(points.target, { x: 415, y: 278 })
})

test('drag line leaves the free endpoint at the pointer when there is no target handle', () => {
  const points = connection_points(
    { x: 120, y: 40 },
    { type: 'source', width: 80, height: 24 },
    { x: 415, y: 278 },
    null,
  )

  assert.deepEqual(points.target, { x: 415, y: 278 })
})
