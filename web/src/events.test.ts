import assert from 'node:assert/strict'
import test from 'node:test'
import { globalWeatherCountryCode, nearestCommodityBenchmarks, toWeatherEvent, type Contract, type GlobalWeather } from './events.ts'

const row: GlobalWeather = {
  point_id: 'glb_tokyo',
  name: 'Tokyo',
  country: 'JP',
  latitude: 35.68,
  longitude: 139.69,
  valid_time: '2026-11-14T12:00:00Z',
  issued_at: '2026-11-14T06:00:00Z',
  temp_f: 70,
  apparent_temp_f: 74,
  wind_10m_mph: 8,
}

test('world weather rows become validated, clickable pip events', () => {
  const event = toWeatherEvent(row)
  assert.equal(event?.value, '70°F')
  assert.equal(event?.trend, 'rising')
  assert.equal(event?.country, '392')
  assert.match(event?.body ?? '', /feels like 74°F/)
  assert.equal(toWeatherEvent({ ...row, country: 'AE' })?.country, '784')
  assert.equal(toWeatherEvent({ ...row, apparent_temp_f: 65 })?.trend, 'easing')
  assert.equal(toWeatherEvent({ ...row, temp_f: null }), null)
  assert.equal(toWeatherEvent({ ...row, latitude: 91 }), null)
})

test('global weather country resolution includes Natural Earth aliases and rejects unknown names', () => {
  assert.equal(globalWeatherCountryCode('Dem. Rep. Congo'), 'CD')
  assert.equal(globalWeatherCountryCode('United States of America'), 'US')
  assert.equal(globalWeatherCountryCode('Chad'), 'TD')
  assert.equal(globalWeatherCountryCode('Atlantis'), null)
  assert.equal(globalWeatherCountryCode('constructor'), null)
})

test('country commodity prices use the nearest mapped benchmark', () => {
  const contracts = [{ symbol: 'BZ=F' } as Contract, { symbol: 'CL=F' } as Contract]
  assert.deepEqual(nearestCommodityBenchmarks(contracts, { lat: 30, lng: -97 }).map((c) => c.symbol), ['CL=F'])
  assert.deepEqual(nearestCommodityBenchmarks(contracts, { lat: 55, lng: 0 }).map((c) => c.symbol), ['BZ=F'])
  assert.equal(nearestCommodityBenchmarks(contracts, null).length, 2)
  assert.deepEqual(nearestCommodityBenchmarks([], { lat: 0, lng: 0 }), [])
})
