import assert from 'node:assert/strict'
import test from 'node:test'
import { distanceKm, globalWeatherCountryCode, regionalCommodityBenchmarks, tradingRegion, toWeatherEvent, type Contract, type GlobalWeather } from './events.ts'

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

test('country commodity prices use the nearest benchmark', () => {
  const contracts = [{ symbol: 'BZ=F' }, { symbol: 'CL=F' }, { symbol: 'DBL=F' }] as Contract[]
  const syms = (at: { lat: number; lng: number } | null) => regionalCommodityBenchmarks(contracts, at).map((c) => c.symbol)
  assert.deepEqual(syms({ lat: 30, lng: -97 }), ['CL=F'])
  assert.deepEqual(syms({ lat: 55, lng: 0 }), ['BZ=F'])
  assert.deepEqual(syms({ lat: 32.4, lng: 53.7 }), ['DBL=F']) // Iran prices off Dubai, not the North Sea
  assert.deepEqual(syms({ lat: 24, lng: 45 }), ['DBL=F']) // Saudi Arabia
  assert.equal(syms(null).length, 3)
  assert.deepEqual(regionalCommodityBenchmarks([], { lat: 0, lng: 0 }), [])
  assert.deepEqual(syms({ lat: 32.9, lng: 13.2 }), ['BZ=F']) // Tripoli: North Sea 2,900 km, Gulf 4,100 km
  assert.deepEqual(syms({ lat: 24.2, lng: 23.3 }), ['DBL=F']) // Kufra, south-east Libya: the Gulf is nearer
  assert.deepEqual(syms({ lat: 26.8, lng: 30.8 }), ['DBL=F']) // Egypt
  assert.deepEqual(syms({ lat: 10, lng: -66.9 }), ['CL=F']) // Venezuela
  const gas = [{ symbol: 'TTF=F' }, { symbol: 'NG=F' }, { symbol: 'JKM=F' }, { symbol: 'HO=F' }] as Contract[]
  const gasAt = (lat: number, lng: number) => regionalCommodityBenchmarks(gas, { lat, lng }).map((c) => c.symbol)
  assert.deepEqual(gasAt(52, 19), ['TTF=F']) // Poland
  assert.deepEqual(gasAt(1.3, 103.8), ['JKM=F']) // Singapore
  assert.deepEqual(gasAt(19.4, -99.1), ['NG=F', 'HO=F']) // Mexico, plus the US-only heating oil
  assert.ok(Math.abs(distanceKm({ lat: 0, lng: 0 }, { lat: 0, lng: 90 }) - 10007.5) < 1)
  assert.equal(tradingRegion(26.8, 30.8), 'europe-africa')
  assert.equal(tradingRegion(35, 105), 'gulf-asia')
  const metals = [{ symbol: 'GC=F' }, { symbol: 'HRC=F' }] as Contract[]
  assert.deepEqual(regionalCommodityBenchmarks(metals, { lat: 32.4, lng: 53.7 }).map((c) => c.symbol), ['GC=F']) // US-only steel dropped
  const hogs = [{ symbol: 'HE=F' }] as Contract[]
  assert.equal(regionalCommodityBenchmarks(hogs, { lat: 32.4, lng: 53.7 }).length, 1) // no local contract: fall back to all
})
