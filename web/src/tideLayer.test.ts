import assert from 'node:assert/strict'
import test from 'node:test'
import { tideAt, tideRange, type TideEvent } from './tideLayer.ts'

const events: TideEvent[] = [
  { t: '2026-11-15T00:00:00Z', v: 0.2, type: 'L' },
  { t: '2026-11-15T06:00:00Z', v: 2.2, type: 'H' },
  { t: '2026-11-15T12:00:00Z', v: 0.4, type: 'L' },
]
const at = (iso: string) => Date.parse(iso)

test('mid-flood is half full, rising and running hardest', () => {
  const s = tideAt(events, at('2026-11-15T03:00:00Z'))!
  assert.ok(Math.abs(s.level - 1.2) < 1e-9)
  assert.ok(Math.abs(s.fill - 0.5) < 1e-9)
  assert.equal(s.rising, true)
  assert.ok(Math.abs(s.flow - 1) < 1e-9)
  assert.equal(s.next.type, 'H')
})

test('high water is full and slack; the ebb drains the fill', () => {
  const high = tideAt(events, at('2026-11-15T06:00:00Z'))!
  assert.ok(high.fill > 0.999 && high.flow < 1e-9)
  const ebb = tideAt(events, at('2026-11-15T09:00:00Z'))!
  assert.equal(ebb.rising, false)
  assert.ok(Math.abs(ebb.fill - 0.5) < 1e-9)
  assert.ok(ebb.rate < 0)
})

test('outside the predictions there is no state; range spans the window', () => {
  assert.equal(tideAt(events, at('2026-11-15T13:00:00Z')), null)
  assert.ok(Math.abs(tideRange(events) - 2.0) < 1e-9)
})
