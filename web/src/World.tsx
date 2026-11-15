import { lazy, Suspense, useCallback, useEffect, useMemo, useState, useRef } from 'react'
import { Link } from 'react-router-dom'
import { useApi } from './api'
import Assistant from './Assistant'
import CommodityPanel from './CommodityPanel'
import CountryPanel from './CountryPanel'
import FocusPanel, { Card } from './FocusPanel'
import LayerMenu, { type Layer } from './LayerMenu'
import type { PlaneDot, ShipDot, TrackPoint } from './Earth'
import PlanePanel from './PlanePanel'
import ShipPanel from './ShipPanel'
import { COMMODITIES, toCommodityEvents, toEvent, toWeatherEvent, type Commodity, type Contract, type GlobalWeather, type RegionSignal, type SignalEvent } from './events'
import type { MarineGrid, WeatherGrid } from './weatherLayer'

const Earth = lazy(() => import('./Earth'))

/** One aircraft from GET /api/flights/global (flights.py normalize_state). */
type Aircraft = {
  hex: string
  callsign: string | null
  lat: number
  lon: number
  altitude_ft: number | null
  on_ground: boolean
  ground_speed_kt: number | null
  track_deg: number | null
  origin_country: string | null
  cargo_operator: string | null
  seen_seconds_ago: number | null
}

const toPlane = (a: Aircraft): PlaneDot => ({
  hex: a.hex,
  mmsi: parseInt(a.hex, 16) || 0,
  name: a.callsign,
  lat: a.lat,
  lon: a.lon,
  category: [
    a.cargo_operator ?? a.origin_country ?? 'Unknown operator',
    a.altitude_ft != null && `${a.altitude_ft.toLocaleString()} ft`,
    a.ground_speed_kt != null && `${Math.round(a.ground_speed_kt)} kt`,
  ].filter(Boolean).join(' · '),
  heading: a.track_deg,
  course: a.track_deg,
  speed_kn: a.ground_speed_kt,
  age_s: a.seen_seconds_ago ?? 0,
  cargo: !!a.cargo_operator,
})

export default function World({ greekMode = false }: { greekMode?: boolean }) {
  const api = useApi()
  const [events, setEvents] = useState<SignalEvent[]>([])
  const [status, setStatus] = useState<'loading' | 'ready' | string>('loading')
  const [selected, setSelected] = useState<SignalEvent | null>(null)
  const [country, setCountry] = useState<string | null>(null)
  const [focusAt, setFocusAt] = useState<{ lat: number; lng: number } | null>(null)
  const [showGrid, setShowGrid] = useState(true)
  const [weatherEvents, setWeatherEvents] = useState<SignalEvent[]>([])
  const [weatherStatus, setWeatherStatus] = useState<'loading' | 'ready' | string>('loading')
  const [showWeather, setShowWeather] = useState(true)
  const [showShips, setShowShips] = useState(true)
  const [ships, setShips] = useState<ShipDot[]>([])
  const [shipInfo, setShipInfo] = useState<{ tracked: number; connected: boolean } | null>(null)
  const [mmsi, setMmsi] = useState<number | null>(null)
  const [track, setTrack] = useState<TrackPoint[]>([])
  const [showCommodities, setShowCommodities] = useState(true)
  const [showPlanes, setShowPlanes] = useState(true)
  const [cargoOnly, setCargoOnly] = useState(true)
  const [aircraft, setAircraft] = useState<Aircraft[]>([])
  const planes = useMemo(() => aircraft.map(toPlane), [aircraft])
  const [planeHex, setPlaneHex] = useState<string | null>(null)
  const [planeNote, setPlaneNote] = useState<string | null>(null)
  const [commodityEvents, setCommodityEvents] = useState<SignalEvent[]>([])
  const [commodityError, setCommodityError] = useState<string | null>(null)
  const [view, setView] = useState<'world' | 'commodities'>('world')
  const [activeCommodity, setActiveCommodity] = useState<Commodity>('oil')
  const [weatherGrid, setWeatherGrid] = useState<WeatherGrid | null>(null)
  const [weatherGridStatus, setWeatherGridStatus] = useState<'loading' | 'ready' | string>('loading')
  const [marineGrid, setMarineGrid] = useState<MarineGrid | null>(null)
  const [marineStatus, setMarineStatus] = useState<'idle' | 'loading' | 'ready' | string>('idle')
  const [weatherLayer, setWeatherLayer] = useState<'off' | 'weather' | 'wind'>('weather')

  useEffect(() => {
    let live = true
    api<RegionSignal[]>('/signals')
      .then((rows) => {
        if (!live) return
        setEvents(rows.map(toEvent))
        setStatus('ready')
      })
      .catch((e: Error) => live && setStatus(e.message))
    return () => {
      live = false
    }
  }, [api])

  useEffect(() => {
    let live = true
    api<GlobalWeather[]>('/weather/global')
      .then((rows) => {
        if (!live) return
        setWeatherEvents(rows.map(toWeatherEvent).filter((event): event is SignalEvent => event !== null))
        setWeatherStatus('ready')
      })
      .catch((e: Error) => live && setWeatherStatus(e.message))
    return () => {
      live = false
    }
  }, [api])

  useEffect(() => {
    let live = true
    api<WeatherGrid>('/weather/grid')
      .then((grid) => {
        if (!live) return
        setWeatherGrid(grid)
        setWeatherGridStatus('ready')
      })
      .catch((e: Error) => live && setWeatherGridStatus(e.message))
    return () => { live = false }
  }, [api])

  useEffect(() => {
    if (weatherLayer !== 'wind') {
      setMarineStatus('idle')
      return
    }
    let live = true
    setMarineStatus('loading')
    api<MarineGrid>('/weather/marine')
      .then((grid) => {
        if (!live) return
        setMarineGrid(grid)
        setMarineStatus('ready')
      })
      .catch((e: Error) => live && setMarineStatus(e.message))
    return () => { live = false }
  }, [api, weatherLayer])

  const [motion, setMotion] = useState(1)
  const motionRef = useRef(motion)
  const changeMotion = (m: number) => {
    motionRef.current = m
    setMotion(m)
  }
  useEffect(() => {
    if (!showShips) {
      setShips([])
      return
    }
    let live = true
    const load = () => {
      if (document.hidden) return // no polling in a background tab
      if (motionRef.current > 1) return // time-lapse projects from the frozen fixes; a refresh would snap ships back
      api<{ ships: ShipDot[]; tracked: number; connected: boolean }>('/ships')
        .then((r) => {
          if (!live) return
          setShips(r.ships)
          setShipInfo({ tracked: r.tracked, connected: r.connected })
        })
        .catch(() => live && setShipInfo(null))
    }
    load()
    const timer = setInterval(load, 30000)
    return () => {
      live = false
      clearInterval(timer)
    }
  }, [api, showShips])

  useEffect(() => {
    if (!showPlanes) {
      setAircraft([])
      return
    }
    let live = true
    const load = () => {
      if (document.hidden) return
      api<{ aircraft: Aircraft[]; stale_error: string | null }>(`/flights/global${cargoOnly ? '?cargo=true' : ''}`)
        .then((r) => {
          if (!live) return
          setAircraft(r.aircraft.filter((a) => !a.on_ground))
          setPlaneNote(r.stale_error ? `Showing the last snapshot: ${r.stale_error}` : null)
        })
        .catch((e: Error) => live && setPlaneNote(e.message))
    }
    load()
    const timer = setInterval(load, 90000) // the server refreshes its OpenSky snapshot every 90 s
    return () => {
      live = false
      clearInterval(timer)
    }
  }, [api, showPlanes, cargoOnly])

  useEffect(() => {
    if (!showCommodities) {
      setCommodityEvents([])
      return
    }
    let live = true
    const keys = Object.keys(COMMODITIES) as Commodity[]
    // allSettled: one commodity's feed failing must not hide the others.
    Promise.allSettled(keys.map((k) => api<{ contracts: Contract[] }>(`/commodities/${k}`))).then((results) => {
      if (!live) return
      setCommodityEvents(results.flatMap((r, i) => (r.status === 'fulfilled' ? toCommodityEvents(keys[i], r.value.contracts) : [])))
      const failed = results.filter((r) => r.status === 'rejected').length
      setCommodityError(failed ? `${failed} of ${keys.length} commodity feeds unavailable` : null)
    })
    return () => {
      live = false
    }
  }, [api, showCommodities])
  const markers = useMemo(() => [...(showGrid ? events : []), ...(showWeather ? weatherEvents : []), ...commodityEvents], [showGrid, showWeather, events, weatherEvents, commodityEvents])

  // One side panel at a time: a marker's signal, a country, a ship or a plane.
  const clearFocus = useCallback(() => {
    setSelected(null)
    setCountry(null)
    setMmsi(null)
    setPlaneHex(null)
    setFocusAt(null)
  }, [])
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') clearFocus()
    }
    addEventListener('keydown', onKey)
    return () => removeEventListener('keydown', onKey)
  }, [clearFocus])

  const pickEvent = useCallback((e: SignalEvent) => {
    setCountry(null)
    setMmsi(null)
    setPlaneHex(null)
    setSelected(e)
    setFocusAt({ lat: e.lat, lng: e.lng })
  }, [])
  const selectEvent = useCallback((e: SignalEvent) => {
    if (e.commodity) setActiveCommodity(e.commodity)
    pickEvent(e)
  }, [pickEvent])
  const pickCountry = useCallback((name: string, at?: { lat: number; lng: number }) => {
    setSelected(null)
    setMmsi(null)
    setPlaneHex(null)
    setCountry(name)
    setFocusAt(at ?? null)
  }, [])
  const shipsRef = useRef(ships)
  shipsRef.current = ships
  const pickShip = useCallback((id: number) => {
    setSelected(null)
    setCountry(null)
    setPlaneHex(null)
    setMmsi(id)
    const s = shipsRef.current.find((x) => x.mmsi === id)
    if (s) setFocusAt({ lat: s.lat, lng: s.lon })
  }, [])
  const pickPlane = useCallback((hex: string) => {
    setSelected(null)
    setCountry(null)
    setMmsi(null)
    setPlaneHex(hex)
    const a = aircraft.find((x) => x.hex === hex)
    if (a) setFocusAt({ lat: a.lat, lng: a.lon })
  }, [aircraft])
  const focused = !!(selected || country || mmsi || planeHex)

  const layers: Layer[] = [
    {
      id: 'grid',
      label: 'Grid demand signals',
      icon: <span className="pip-mark rising" />,
      color: '#ff6b6b',
      on: showGrid,
      onChange: setShowGrid,
      note: status === 'loading' ? 'Loading…' : status === 'ready'
        ? `${events.length} US regions furthest from EIA's forecast. ▲ above, ▼ below forecast.`
        : <span className="error" role="alert">{status}</span>,
    },
    {
      id: 'weather',
      label: 'World weather',
      icon: <span className="pip-mark rising" />,
      color: '#ff6b6b',
      on: showWeather,
      onChange: setShowWeather,
      note: weatherStatus === 'loading' ? 'Loading…' : weatherStatus === 'ready'
        ? `${weatherEvents.length} city forecasts. ▲ feels warmer than air temperature; ▼ otherwise.`
        : <span className="error" role="alert">{weatherStatus}</span>,
    },
    {
      id: 'ships',
       label: greekMode ? 'Longships' : 'Live ships',
      icon: <span className="ship-dot" />,
      color: '#ffc77a',
      on: showShips,
      onChange: setShowShips,
       note: (
         <>
            {greekMode
              ? shipInfo?.connected ? `${ships.length} live AIS vessels shown as longships` : 'Live vessel positions (AIS)'
              : shipInfo
                ? shipInfo.connected ? `${ships.length} cargo and tanker vessels heard via AIS` : 'AIS feed reconnecting…'
                : 'Cargo and tanker vessel types (AIS)'}
            {!greekMode && (
              <span className="ship-legend" role="list" aria-label="Ship colors">
                <span role="listitem"><i className="ship-legend-dot cargo" aria-hidden="true" />Cargo</span>
                <span role="listitem"><i className="ship-legend-dot tanker" aria-hidden="true" />Tankers</span>
                <span role="listitem"><i className="ship-legend-dot other" aria-hidden="true" />Other / unknown</span>
              </span>
            )}
         </>
       ),
      extra: (
        <div className="motion" role="radiogroup" aria-label="Ship motion speed">
          {[1, 60, 600].map((m) => (
            <button key={m} role="radio" aria-checked={motion === m} className={motion === m ? 'on' : ''} onClick={() => changeMotion(m)}>
              {m === 1 ? 'Live' : `×${m}`}
            </button>
          ))}
          <span className="micro">
            {motion === 1 ? 'Real speed and course.' : `Time-lapse, projected ${motion}× from last reports. Live updates paused.`}
          </span>
        </div>
      ),
    },
    {
      id: 'planes',
      label: 'Live flights',
      icon: <span className="plane-dot" />,
      color: '#8fd3ff',
      on: showPlanes,
      onChange: setShowPlanes,
      note: planeNote
        ? <span className="error" role="alert">{planeNote}</span>
        : planes.length
          ? `${planes.length.toLocaleString()} airborne, ${planes.filter((p) => p.cargo).length} cargo (amber). OpenSky, every 90 s.`
          : 'Aircraft worldwide (ADS-B)',
      extra: (
        <div className="motion" role="radiogroup" aria-label="Which flights">
          {[false, true].map((c) => (
            <button key={String(c)} role="radio" aria-checked={cargoOnly === c} className={cargoOnly === c ? 'on' : ''} onClick={() => setCargoOnly(c)}>
              {c ? 'Cargo only' : 'All'}
            </button>
          ))}
        </div>
      ),
    },
    {
      id: 'commodities',
      label: 'Commodity futures',
      icon: <span className="pip-mark commodity rising" />,
      color: '#ff6b6b',
      on: showCommodities,
      onChange: setShowCommodities,
      note: commodityError ?? '30-day change at each benchmark’s pricing point',
    },
  ]

  return (
    <div className={`world${focused ? ' focused' : ''}`}>
      <div className="world-intro">
        <p className="kicker">{view === 'world' ? 'Global editorial view' : 'Commodity monitoring'}</p>
        <h1>{view === 'world' ? 'World view' : 'Commodities'}</h1>
        <p className="muted">
          {view === 'world'
            ? 'Click any country for its briefing, news, filings and markets.'
            : 'Click a country for commodity markets, funds and nearby benchmark prices.'}
        </p>
      </div>

      <div className="view-controls" aria-label="Globe views and weather">
        <div className="view-switch" role="group" aria-label="Globe view">
          {(['world', 'commodities'] as const).map((next) => (
            <button key={next} type="button" aria-pressed={view === next}
              className={view === next ? 'on' : ''} onClick={() => setView(next)}>
              {next === 'world' ? 'World' : 'Commodities'}
            </button>
          ))}
        </div>
        {view === 'commodities' && (
          <label className="commodity-select">
            <span className="sr-only">Active commodity</span>
            <select aria-label="Active commodity" value={activeCommodity}
              onChange={(e) => setActiveCommodity(e.target.value as Commodity)}>
              {(Object.keys(COMMODITIES) as Commodity[]).map((key) => <option key={key} value={key}>{COMMODITIES[key]}</option>)}
            </select>
          </label>
        )}
        <label className="commodity-select">
          <span className="sr-only">Weather layer</span>
          <select aria-label="Weather layer" value={weatherLayer}
            onChange={(e) => setWeatherLayer(e.target.value as typeof weatherLayer)}>
            <option value="off">Off</option>
            <option value="weather">Weather</option>
            <option value="wind">Wind &amp; Surf</option>
          </select>
        </label>
        {weatherLayer !== 'off' && <p className="weather-note" aria-live="polite">
          {weatherGridStatus === 'loading' ? 'Loading weather…'
            : weatherGridStatus !== 'ready' ? `Weather unavailable: ${weatherGridStatus}`
              : weatherLayer === 'weather' ? `Temperature · rain${weatherGrid?.stale ? ' · last snapshot' : ''}`
                : marineStatus === 'loading' ? 'Loading wave heights…'
                  : marineStatus !== 'ready' ? `Wind · surf unavailable: ${marineStatus}`
                    : `Wind · wave height${weatherGrid?.stale || marineGrid?.stale ? ' · last snapshot' : ''}`}
        </p>}
      </div>

      <LayerMenu layers={layers} />

      <Suspense fallback={null}>
        <Earth events={markers} selected={selected} onSelect={selectEvent} onCountry={pickCountry} ships={ships} onShip={pickShip} planes={planes} onPlane={pickPlane} motion={motion} track={mmsi ? track : undefined} focusAt={focused ? focusAt : null} greekMode={greekMode}
          weather={weatherLayer === 'off' ? null : weatherGrid} marine={marineGrid}
          windAndSurf={weatherLayer === 'wind' && marineStatus === 'ready'} />
      </Suspense>

      {mmsi && <ShipPanel key={mmsi} mmsi={mmsi} onTrack={setTrack} onClose={clearFocus} />}
      {planeHex && <PlanePanel key={planeHex} hex={planeHex} aircraft={aircraft.find((a) => a.hex === planeHex) ?? null} onClose={clearFocus} />}

      {country && <CountryPanel key={country} country={country} onClose={clearFocus} mode={view}
        commodity={activeCommodity} coordinates={view === 'commodities' ? focusAt : null} />}

      {selected?.commodity && <CommodityPanel key={selected.commodity} commodity={selected.commodity} onClose={clearFocus} />}

      {selected && !selected.commodity && (
        <FocusPanel
          label={selected.title}
          kicker={selected.kicker}
          title={selected.title}
          onClose={clearFocus}
          hero={
            <div className="signal-hero">
              <div>
                <p className={`figure ${selected.trend}`}>{selected.value}</p>
                <p className="micro">{selected.valueLabel}</p>
              </div>
              <p className="muted">{selected.body}</p>
            </div>
          }
        >
          <Card title="Source context" span={2}>
            <p className="sources">{selected.sources}</p>
          </Card>
          <Card title="Dig deeper" span={2}>
            <Link className="button" to={`/search?q=${encodeURIComponent(selected.query)}`}>Search live data</Link>
          </Card>
        </FocusPanel>
      )}

      {!focused && <Assistant />}
    </div>
  )
}
