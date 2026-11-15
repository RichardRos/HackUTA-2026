import { lazy, Suspense, useCallback, useEffect, useMemo, useState, useRef } from 'react'
import { Link } from 'react-router-dom'
import { useApi } from './api'
import Assistant from './Assistant'
import CommodityPanel from './CommodityPanel'
import CountryPanel from './CountryPanel'
import FocusPanel, { Card } from './FocusPanel'
import GreekLore from './greek/GreekLore'
import GreekSky from './greek/GreekSky'
import type { LoreEntry } from './greek/myth'
import LayerMenu, { type Layer } from './LayerMenu'
import type { PlaneDot, ShipDot, TrackPoint } from './Earth'
import PlanePanel from './PlanePanel'
import ShipPanel from './ShipPanel'
import TidePanel from './TidePanel'
import type { TideOverview, TideStation } from './tideLayer'
import { COMMODITIES, toCommodityEvents, toEvent, toWeatherEvent, type Commodity, type Contract, type GlobalWeather, type RegionSignal, type SignalEvent } from './events'
import type { MarineGrid, WeatherGrid } from './weatherLayer'

const Earth = lazy(() => import('./Earth'))
const NO_TIDES: TideStation[] = []

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
  const [localAt, setLocalAt] = useState<{ lat: number; lng: number } | null>(null) // deep-zoom click: regional news
  const [showGrid, setShowGrid] = useState(true)
  const [weatherEvents, setWeatherEvents] = useState<SignalEvent[]>([])
  const [weatherStatus, setWeatherStatus] = useState<'loading' | 'ready' | string>('loading')
  const [showWeather, setShowWeather] = useState(true)
  const [showShips, setShowShips] = useState(true)
  const [ships, setShips] = useState<ShipDot[]>([])
  const [mmsi, setMmsi] = useState<number | null>(null)
  const [track, setTrack] = useState<TrackPoint[]>([])
  const [showCommodities, setShowCommodities] = useState(true)
  const [showPlanes, setShowPlanes] = useState(true)
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
  const [showTides, setShowTides] = useState(true)
  const [tides, setTides] = useState<TideStation[]>([])
  const [tideStatus, setTideStatus] = useState<'idle' | 'loading' | 'ready' | string>('idle')
  const [tideId, setTideId] = useState<string | null>(null)

  // Greek mode is strictly seafaring: ships, tides, wind and waves. Flights, oil and the other commodities,
  // grid signals, city weather and country briefings are hidden. The user's own switches are kept for
  // when Greek mode is turned off again.
  const gridOn = showGrid && !greekMode
  const cityWeatherOn = showWeather && !greekMode
  const planesOn = showPlanes && !greekMode
  const commoditiesOn = showCommodities && !greekMode
  const activeView = greekMode ? 'world' : view
  const overlay = greekMode && weatherLayer === 'weather' ? 'wind' : weatherLayer
  useEffect(() => {
    if (!greekMode) return
    setSelected(null)
    setCountry(null)
    setPlaneHex(null)
  }, [greekMode])

  // Greek mode: the bestiary, Odysseus' voyage, and the legend open on the parchment card.
  const [showMonsters, setShowMonsters] = useState(true)
  const [showVoyage, setShowVoyage] = useState(true)
  const [lore, setLore] = useState<LoreEntry | null>(null)

  useEffect(() => {
    if (!showTides) {
      setTideStatus('idle')
      return
    }
    let live = true
    setTideStatus('loading')
    // A cold server cache takes ~25 s (hundreds of NOAA stations). Predictions cover yesterday to +2 days, so one load lasts the session; refresh every 6 h anyway.
    const load = () => api<TideOverview>('/tides')
      .then((r) => {
        if (!live) return
        setTides(r.stations)
        setTideStatus('ready')
      })
      .catch((e: Error) => live && setTideStatus(e.message))
    load()
    const timer = setInterval(load, 6 * 3_600_000)
    return () => {
      live = false
      clearInterval(timer)
    }
  }, [api, showTides])

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
    if (overlay !== 'wind') {
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
  }, [api, overlay])

  useEffect(() => {
    if (!showShips) {
      setShips([])
      return
    }
    let live = true
    const load = () => {
      if (document.hidden) return // no polling in a background tab
      api<{ ships: ShipDot[] }>('/ships')
        .then((r) => live && setShips(r.ships))
        .catch(() => {}) // keep the last positions; the next poll retries
    }
    load()
    const timer = setInterval(load, 30000)
    return () => {
      live = false
      clearInterval(timer)
    }
  }, [api, showShips])

  useEffect(() => {
    if (!planesOn) {
      setAircraft([])
      return
    }
    let live = true
    const load = () => {
      if (document.hidden) return
      api<{ aircraft: Aircraft[]; stale_error: string | null }>('/flights/global?cargo=true')
        .then((r) => {
          if (!live) return
          setAircraft(r.aircraft.filter((a) => !a.on_ground))
          setPlaneNote(null)
        })
        .catch((e: Error) => live && setPlaneNote(e.message))
    }
    load()
    const timer = setInterval(load, 90000) // the server refreshes its OpenSky snapshot every 90 s
    return () => {
      live = false
      clearInterval(timer)
    }
  }, [api, planesOn])

  useEffect(() => {
    if (!commoditiesOn) {
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
  }, [api, commoditiesOn])
  const markers = useMemo(
    () => [...(gridOn ? events : []), ...(cityWeatherOn ? weatherEvents : []), ...(commoditiesOn ? commodityEvents : [])],
    [gridOn, cityWeatherOn, commoditiesOn, events, weatherEvents, commodityEvents],
  )

  // One side panel at a time: a marker's signal, a country, a ship, a plane, a tide gauge or a legend.
  const clearFocus = useCallback(() => {
    setSelected(null)
    setCountry(null)
    setMmsi(null)
    setPlaneHex(null)
    setTideId(null)
    setFocusAt(null)
    setLocalAt(null)
    setLore(null)
  }, [])
  const openLore = useCallback((entry: LoreEntry) => {
    clearFocus()
    setLore(entry)
  }, [clearFocus])
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
    setTideId(null)
    setSelected(e)
    setFocusAt({ lat: e.lat, lng: e.lng })
  }, [])
  const selectEvent = useCallback((e: SignalEvent) => {
    if (e.commodity) setActiveCommodity(e.commodity)
    pickEvent(e)
  }, [pickEvent])
  const pickCountry = useCallback((name: string, at?: { lat: number; lng: number }, deep = false) => {
    setSelected(null)
    setMmsi(null)
    setPlaneHex(null)
    setTideId(null)
    setCountry(name)
    setFocusAt(at ?? null)
    setLocalAt(deep && at ? at : null)
  }, [])
  const shipsRef = useRef(ships)
  shipsRef.current = ships
  const pickShip = useCallback((id: number) => {
    setSelected(null)
    setCountry(null)
    setPlaneHex(null)
    setTideId(null)
    setMmsi(id)
    const s = shipsRef.current.find((x) => x.mmsi === id)
    if (s) setFocusAt({ lat: s.lat, lng: s.lon })
  }, [])
  const pickPlane = useCallback((hex: string) => {
    setSelected(null)
    setCountry(null)
    setMmsi(null)
    setTideId(null)
    setPlaneHex(hex)
    const a = aircraft.find((x) => x.hex === hex)
    if (a) setFocusAt({ lat: a.lat, lng: a.lon })
  }, [aircraft])
  const tidesRef = useRef(tides)
  tidesRef.current = tides
  const pickTide = useCallback((id: string) => {
    setSelected(null)
    setCountry(null)
    setMmsi(null)
    setPlaneHex(null)
    setTideId(id)
    const t = tidesRef.current.find((x) => x.id === id)
    if (t) setFocusAt({ lat: t.lat, lng: t.lon })
  }, [])
  const tideStation = tideId ? tides.find((t) => t.id === tideId) ?? null : null
  const focused = !!(selected || country || mmsi || planeHex || tideStation)
  const ship = mmsi ? ships.find((s) => s.mmsi === mmsi) : undefined
  const plane = planeHex ? aircraft.find((a) => a.hex === planeHex) : undefined
  const at = (lat: number, lng: number) => `${lat.toFixed(3)}, ${lng.toFixed(3)}`
  // what the assistant is told is on screen; ids and coordinates let it call the MCP tools itself
  const onScreen: { topic: string; context: string } | null =
    selected ? { topic: selected.title, context: `${selected.kicker}: ${selected.title}. ${selected.value} (${selected.valueLabel}). ${selected.body} At ${at(selected.lat, selected.lng)}.${selected.commodity ? ` Commodity: ${selected.commodity}.` : ''}` }
      : country ? { topic: country, context: `Country dashboard for ${country} (Natural Earth name), ${view === 'commodities' ? `commodities view, active commodity ${activeCommodity}` : 'world view: briefing, news, markets, filings, Treasury, weather'}.${localAt ? ` Zoomed in on ${at(localAt.lat, localAt.lng)}: the News card shows regional stories for that point.` : focusAt ? ` Clicked at ${at(focusAt.lat, focusAt.lng)}.` : ''}` }
        : mmsi ? { topic: ship?.name || `ship ${mmsi}`, context: `Ship panel: MMSI ${mmsi}${ship ? `, ${ship.name ?? 'unnamed'}, ${ship.category}, at ${at(ship.lat, ship.lon)}, ${ship.speed_kn ?? '?'} kn, course ${ship.course ?? '?'}°` : ''}.` }
          : planeHex ? { topic: plane?.callsign?.trim() || `aircraft ${planeHex}`, context: `Aircraft panel: ICAO hex ${planeHex}${plane ? `, callsign ${plane.callsign ?? 'none'}, ${plane.cargo_operator ? `cargo operator ${plane.cargo_operator}, ` : ''}from ${plane.origin_country ?? '?'}, at ${at(plane.lat, plane.lon)}, ${plane.altitude_ft ?? '?'} ft` : ''}.` }
            : tideStation ? { topic: tideStation.name, context: `Tide station panel: NOAA station ${tideStation.id}, ${tideStation.name}, at ${at(tideStation.lat, tideStation.lon)}.` }
              : null

  const failed = (st: string) => (st === 'idle' || st === 'loading' || st === 'ready' ? null : st)
  const layers: Layer[] = [
    { id: 'grid', label: 'Grid demand', icon: <span className="pip-mark rising" />, color: '#ff6b6b', on: showGrid, onChange: setShowGrid, error: failed(status) },
    { id: 'weather', label: 'Weather', icon: <span className="pip-mark rising" />, color: '#ff6b6b', on: showWeather, onChange: setShowWeather, error: failed(weatherStatus) },
    { id: 'ships', label: greekMode ? 'Triremes & longships' : 'Ships', icon: <span className="ship-dot" />, color: '#ffc77a', on: showShips, onChange: setShowShips },
    { id: 'planes', label: 'Cargo flights', icon: <span className="plane-dot" />, color: '#8fd3ff', on: showPlanes, onChange: setShowPlanes, error: planeNote },
    { id: 'tides', label: 'Tides', icon: <span className="tide-dot" />, color: '#4ccbd1', on: showTides, onChange: setShowTides, error: failed(tideStatus) },
    { id: 'commodities', label: 'Commodities', icon: <span className="pip-mark commodity rising" />, color: '#ff6b6b', on: showCommodities, onChange: setShowCommodities, error: commodityError },
  ]
  const greekLayers: Layer[] = [
    {
      id: 'bestiary',
      label: 'Bestiary',
      icon: <span className="gm-mini" />,
      color: '#e8c97a',
      on: showMonsters,
      onChange: setShowMonsters,
    },
    {
      id: 'odyssey',
      label: 'The Odyssey',
      icon: <span className="route-mini" />,
      color: '#e8c97a',
      on: showVoyage,
      onChange: setShowVoyage,
    },
  ]
  // Greek mode keeps only the sea: the myth layers, then ships and tides.
  const shownLayers = greekMode ? [...greekLayers, ...layers.filter((l) => l.id === 'ships' || l.id === 'tides')] : layers

  return (
    <div className={`world${focused ? ' focused' : ''}`}>
      {greekMode && <GreekSky />}
      <div className="world-intro">
        {greekMode && <p className="greek-overline" lang="grc">ΘΑΛΑΣΣΑ · ΟΙΝΟΨ ΠΟΝΤΟΣ</p>}
        <p className="kicker">{greekMode ? 'Seafaring view' : activeView === 'world' ? 'Global editorial view' : 'Commodity monitoring'}</p>
        <h1>{greekMode ? 'The wine-dark sea' : activeView === 'world' ? 'World view' : 'Commodities'}</h1>
        <p className="muted">
          {greekMode
            ? 'Ships sail as triremes, tankers as longships. Clay monsters come from the Odyssey, royal blue ones from other myths. Click any for its legend.'
            : activeView === 'world'
              ? 'Click any country for its briefing, news, filings and markets.'
              : 'Click a country for commodity markets, funds and nearby benchmark prices.'}
        </p>
      </div>

      <div className="view-controls" aria-label={greekMode ? 'Sea conditions' : 'Globe views and weather'}>
        {!greekMode && (
          <div className="view-switch" role="group" aria-label="Globe view">
            {(['world', 'commodities'] as const).map((next) => (
              <button key={next} type="button" aria-pressed={view === next}
                className={view === next ? 'on' : ''} onClick={() => setView(next)}>
                {next === 'world' ? 'World' : 'Commodities'}
              </button>
            ))}
          </div>
        )}
        {activeView === 'commodities' && (
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
          <select aria-label="Weather layer" value={overlay}
            onChange={(e) => setWeatherLayer(e.target.value as typeof weatherLayer)}>
            <option value="off">Off</option>
            {!greekMode && <option value="weather">Weather</option>}
            <option value="wind">Wind &amp; Surf</option>
          </select>
        </label>
        {overlay !== 'off' && <p className="weather-note" aria-live="polite">
          {weatherGridStatus === 'loading' ? 'Loading weather…'
            : weatherGridStatus !== 'ready' ? `Weather unavailable: ${weatherGridStatus}`
              : overlay === 'weather' ? `Temperature · rain${weatherGrid?.stale ? ' · last snapshot' : ''}`
                : marineStatus === 'loading' ? 'Loading wave heights…'
                  : marineStatus !== 'ready' ? `Wind · surf unavailable: ${marineStatus}`
                    : `Wind · wave height${weatherGrid?.stale || marineGrid?.stale ? ' · last snapshot' : ''}`}
        </p>}
      </div>

      <LayerMenu layers={shownLayers} />

      <Suspense fallback={null}>
        <Earth events={markers} selected={selected} onSelect={selectEvent} onCountry={greekMode ? undefined : pickCountry} ships={ships} onShip={pickShip}
          planes={planesOn ? planes : undefined} onPlane={pickPlane} track={mmsi ? track : undefined} focusAt={focused ? focusAt : null} greekMode={greekMode}
          weather={overlay === 'off' ? null : weatherGrid} marine={marineGrid}
          temperature={overlay === 'weather'}
          windAndSurf={overlay === 'wind' && marineStatus === 'ready'}
          tides={showTides ? tides : NO_TIDES} onTide={pickTide}
          showMonsters={showMonsters} showVoyage={showVoyage} onLore={openLore} lore={greekMode && !focused ? lore : null} />
      </Suspense>

      {greekMode && lore && !focused && <GreekLore entry={lore} onPick={openLore} onClose={() => setLore(null)} />}

      {mmsi && <ShipPanel key={mmsi} mmsi={mmsi} onTrack={setTrack} onClose={clearFocus} />}
      {tideStation && <TidePanel key={tideStation.id} station={tideStation} onClose={clearFocus} />}
      {planeHex && <PlanePanel key={planeHex} hex={planeHex} aircraft={aircraft.find((a) => a.hex === planeHex) ?? null} onClose={clearFocus} />}

      {country && <CountryPanel key={country} country={country} onClose={clearFocus} mode={activeView}
        commodity={activeCommodity} coordinates={activeView === 'commodities' ? focusAt : localAt} />}

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
          <Card title="Source context">
            <p className="sources">{selected.sources}</p>
          </Card>
          <Card title="Dig deeper">
            <Link className="button" to={`/search?q=${encodeURIComponent(selected.query)}`}>Search live data</Link>
          </Card>
        </FocusPanel>
      )}

      {!(greekMode && lore && !focused) && (
        <Assistant key={`chat:${onScreen?.topic ?? ''}`} topic={onScreen?.topic} context={onScreen?.context.slice(0, 2000)} greekMode={greekMode} />
      )}
    </div>
  )
}
