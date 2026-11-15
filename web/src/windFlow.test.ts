import assert from 'node:assert/strict'
import test from 'node:test'
import { packWind, sampleWind, sampleWindInto, type WindField } from './windFlow.ts'

// 2 rows (lat 0, 10) x 4 columns (lon -180, -90, 0, 90); u = column index, v = row index.
const field: WindField = {
  lats: [0, 10],
  lons: [-180, -90, 0, 90],
  wind_u: [0, 1, 2, 3, 0, 1, 2, 3],
  wind_v: [0, 0, 0, 0, 1, 1, 1, 1],
  wind_ms: [1, 1, 1, 1, 3, 3, 3, 3],
}

test('bilinear between grid points', () => {
  const [u, v, s] = sampleWind(field, 5, -45)!
  assert.ok(Math.abs(u - 1.5) < 1e-9)
  assert.ok(Math.abs(v - 0.5) < 1e-9)
  assert.ok(Math.abs(s - 2) < 1e-9)
})

test('wraps across the date line', () => {
  const [u] = sampleWind(field, 0, 135)! // halfway from lon 90 (u=3) back round to -180 (u=0)
  assert.ok(Math.abs(u - 1.5) < 1e-9)
})

test('off the grid or on a gap is null', () => {
  assert.equal(sampleWind(field, 20, 0), null)
  assert.equal(sampleWind({ ...field, wind_u: [null, 1, 2, 3, 0, 1, 2, 3] }, 5, -170), null)
})

test('packed sampler writes into a reused buffer and falls back to |(u, v)| without wind_ms', () => {
  const packed = packWind({ ...field, wind_ms: [null, null, null, null, null, null, null, null] })
  const out = new Float32Array(3)
  assert.equal(sampleWindInto(packed, 10, 0, out), true)
  assert.deepEqual([...out], [2, 1, Math.fround(Math.hypot(2, 1))])
  assert.equal(sampleWindInto(packed, -1, 0, out), false)
})
