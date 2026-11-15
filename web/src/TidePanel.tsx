import { useEffect, useState } from 'react'
import { useApi } from './api'
import { TideChart, type TidePoint } from './Charts'
import FocusPanel, { Card } from './FocusPanel'
import { tideAt, type TideStation } from './tideLayer'

/** GET /api/tides/{id} (tides.py tide_station). Times UTC, heights metres above the station's datum. */
type TideDetail = {
  station: Omit<TideStation, 'events'>
  predicted: TidePoint[]
  observed: TidePoint[]
  observed_error: string | null
  events: TidePoint[]
}

const fmt = (iso: string) => {
  const d = new Date(iso)
  return `${d.toLocaleDateString([], { weekday: 'short', timeZone: 'UTC' })} ${iso.slice(11, 16)} UTC`
}
const KIND = {
  gauge: 'NOAA live gauge',
  harmonic: 'NOAA tide predictions',
  model: 'Global tide model / Open-Meteo',
} as const
const DATUM = {
  MLLW: 'Heights in metres above mean lower low water (MLLW), the chart datum. Times UTC.',
  MSL: 'Heights in metres relative to mean sea level, from a global tide model (includes surge). Times UTC.',
} as const

const until = (iso: string, now: number) => {
  const min = Math.max(0, Math.round((Date.parse(iso) - now) / 60_000))
  return min < 60 ? `in ${min} min` : `in ${Math.floor(min / 60)} h ${min % 60} min`
}

export default function TidePanel({ station, onClose }: { station: TideStation; onClose: () => void }) {
  const api = useApi()
  const [detail, setDetail] = useState<TideDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    let live = true
    api<TideDetail>(`/tides/${station.id}`)
      .then((d) => live && setDetail(d))
      .catch((e: Error) => live && setError(e.message))
    return () => { live = false }
  }, [api, station.id])
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 30_000)
    return () => clearInterval(timer)
  }, [])

  const events = detail?.events.length ? detail.events : station.events
  const state = tideAt(events, now)
  const upcoming = events.filter((e) => Date.parse(e.t) > now)
  const nextHigh = upcoming.find((e) => e.type === 'H')
  const nextLow = upcoming.find((e) => e.type === 'L')
  const lastObs = detail?.observed[detail.observed.length - 1]
  const predictedAtObs = lastObs ? tideAt(events, Date.parse(lastObs.t)) : null
  const surge = lastObs && predictedAtObs ? lastObs.v - predictedAtObs.level : null
  const day = events.filter((e) => Math.abs(Date.parse(e.t) - now) < 12 * 3_600_000)
  const range = day.length > 1 ? Math.max(...day.map((e) => e.v)) - Math.min(...day.map((e) => e.v)) : null
  const where = station.state ? `${station.name}, ${station.state}` : station.name

  return (
    <FocusPanel
      label={`Tides at ${where}`}
      kicker={station.kind === 'model' ? KIND.model : `Station ${station.id} / ${KIND[station.kind]}`}
      title={where}
      onClose={onClose}
      aside={
        <>
          <h3 className="section-title">Right now</h3>
          <p className={`big tide-dir ${state?.rising ? 'flood' : 'ebb'}`}>
            {state ? <><span aria-hidden="true">{state.rising ? '▲' : '▼'}</span> {state.rising ? 'Rising (flood)' : 'Falling (ebb)'}</> : 'Outside prediction window'}
          </p>
          {state && <p className="muted">
            {state.flow < 0.2 ? 'Near slack water, turning soon.' : `${Math.abs(state.rate).toFixed(2)} m per hour, ${state.flow > 0.8 ? 'running hardest' : state.rising ? 'filling' : 'draining'}.`}
          </p>}
          <p className="micro">{DATUM[station.datum]}</p>
        </>
      }
      hero={
        <ul className="tiles">
          <li><span className="section-title">Water level</span><span className="tile-value">{state ? `${state.level.toFixed(2)} m` : '–'}</span></li>
          <li><span className="section-title">Next high</span><span className="tile-value">{nextHigh ? `${nextHigh.v.toFixed(2)} m` : '–'}</span>
            {nextHigh && <span className="micro">{fmt(nextHigh.t)} · {until(nextHigh.t, now)}</span>}</li>
          <li><span className="section-title">Next low</span><span className="tile-value">{nextLow ? `${nextLow.v.toFixed(2)} m` : '–'}</span>
            {nextLow && <span className="micro">{fmt(nextLow.t)} · {until(nextLow.t, now)}</span>}</li>
        </ul>
      }
    >
      <Card title="Tide curve" span={4}>
        {error ? <p className="error" role="alert">{error}</p>
          : !detail ? <p className="muted">Loading tide curve…</p>
            : detail.predicted.length > 1
              ? <TideChart predicted={detail.predicted} observed={detail.observed} events={detail.events} now={now} datum={station.datum} />
              : <p className="muted">NOAA has no predicted curve for this station right now.</p>}
      </Card>
      <Card title="Station" span={2}>
        <dl className="facts">
          <div><dt>Source</dt><dd>{station.source}</dd></div>
          {station.tide_type && <div><dt>Tide type</dt><dd>{station.tide_type}</dd></div>}
          <div><dt>Range, ±12 h</dt><dd>{range != null ? `${range.toFixed(2)} m` : '–'}</dd></div>
          <div><dt>Position</dt><dd>{station.lat.toFixed(3)}, {station.lon.toFixed(3)}</dd></div>
        </dl>
      </Card>
      <Card title={station.kind === 'gauge' ? 'Observed vs predicted' : 'Live gauge'} span={2}>
        {surge != null && lastObs ? (
          <>
            <p className="big">{surge >= 0 ? '+' : ''}{surge.toFixed(2)} m</p>
            <p className="muted">
              {Math.abs(surge) < 0.1 ? 'Water is tracking the astronomical tide.'
                : surge > 0 ? 'Water is running above prediction: wind, pressure or surge pushing it up.'
                  : 'Water is running below prediction: offshore wind or high pressure holding it down.'}
            </p>
            <p className="micro">Gauge reading {fmt(lastObs.t)}</p>
          </>
        ) : <p className="muted">{
          station.kind === 'model' ? 'Modelled point: there is no tide gauge here, so the curve is the model alone.'
            : station.kind === 'harmonic' ? 'Prediction-only NOAA station: no live gauge to compare against.'
              : detail?.observed_error ? `Gauge offline: ${detail.observed_error}`
                : detail ? 'No recent gauge reading.' : 'Loading…'}</p>}
      </Card>
    </FocusPanel>
  )
}
