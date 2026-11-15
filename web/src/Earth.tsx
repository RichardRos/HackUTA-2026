import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent, type PointerEvent as ReactPointerEvent } from 'react'
import Globe, { type GlobeMethods } from 'react-globe.gl'
import { MeshPhongMaterial } from 'three'
import { feature } from 'topojson-client'
import atlas from 'world-atlas/countries-110m.json'
import type { SignalEvent } from './events'
import { CARGO_PLANE_STYLE, PLANE_STYLE, SHIP_STYLE, TRIREME_STYLE, ShipLayer, type MovingShip } from './shipLayer'
import { TideLayer, type TideStation } from './tideLayer'
import { WeatherOverlay, weatherTexture, type MarineGrid, type WeatherGrid } from './weatherLayer'

type Country = { id: string; properties: { name: string } }

const fmtUtc = (iso: string) => `${iso.slice(11, 16)} UTC`

const LAND = '#1a272d'
const LINE = '#2d3d43'
const ACCENT = '#7bf1b1'
const OCEAN = new MeshPhongMaterial({ color: '#0e1820', specular: '#000000', shininess: 0 })
const DEEP_ALTITUDE = 0.55 // zoomed in this far, a click means a region rather than the whole country
const GREEK_PALETTE = { land: '#ded0bc', line: '#8ca1aa', accent: '#e8c97a', ocean: '#102f46', hover: '#c45c3e' }
const RENDERER = { antialias: true, alpha: true, powerPreference: 'high-performance' as const }

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const COUNTRIES = (feature(atlas as any, (atlas as any).objects.countries) as any).features as Country[]

/** Blend `hex` over the land colour by `t` (0..1). */
function tint(hex: string, t: number, base = LAND) {
  const ch = (c: string, i: number) => parseInt(c.slice(1 + i * 2, 3 + i * 2), 16)
  return `rgb(${[0, 1, 2].map((i) => Math.round(ch(base, i) * (1 - t) + ch(hex, i) * t)).join(',')})`
}
const noSides = () => 'rgba(0,0,0,0)'
const showWhenFacing = (el: HTMLElement, visible: boolean) => {
  el.style.opacity = visible ? '1' : '0'
  el.style.pointerEvents = visible ? 'auto' : 'none'
}

function useSize() {
  const ref = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState({ width: 0, height: 0 })
  useEffect(() => {
    const ro = new ResizeObserver(([e]) =>
      setSize({ width: Math.round(e.contentRect.width), height: Math.round(e.contentRect.height) }),
    )
    if (ref.current) ro.observe(ref.current)
    return () => ro.disconnect()
  }, [])
  return [ref, size] as const
}

type Props = {
  events?: SignalEvent[]
  selected?: SignalEvent | null
  onSelect?: (e: SignalEvent) => void
  onCountry?: (name: string, at?: { lat: number; lng: number }, deep?: boolean) => void
  ships?: ShipDot[]
  onShip?: (mmsi: number) => void
  planes?: PlaneDot[]
  onPlane?: (hex: string) => void
  motion?: number // ship time-lapse multiplier; 1 = real time
  focusAt?: { lat: number; lng: number } | null // centre here, zoomed out for the corner view
  track?: TrackPoint[]
  ambient?: boolean // splash: slow spin, no interaction
  weather?: WeatherGrid | null
  marine?: MarineGrid | null
  windAndSurf?: boolean
  greekMode?: boolean
  tides?: TideStation[] // NOAA tide gauges; empty hides the layer
  tideMotion?: number // tide clock multiplier; 1 = live
  onTide?: (id: string) => void
  onTideClock?: (ms: number) => void // the tide clock, a few times a second in time-lapse
}

export type TrackPoint = { lat: number; lon: number }
export type ShipDot = MovingShip
export type PlaneDot = MovingShip & { cargo: boolean; hex: string } // name = callsign, category = tooltip detail

const NO_EVENTS: SignalEvent[] = []
const NO_SHIPS: ShipDot[] = []
const NO_PLANES: PlaneDot[] = []
const NO_TRACK: TrackPoint[] = []
const NO_TIDES: TideStation[] = []
const SHIP = '#ffc77a' // the Figma's ship amber

export default function Earth({ events = NO_EVENTS, selected, onSelect, onCountry, ships = NO_SHIPS, onShip, planes = NO_PLANES, onPlane, motion = 1, track = NO_TRACK, focusAt = null, ambient = false, weather = null, marine = null, windAndSurf = false, greekMode = false, tides = NO_TIDES, tideMotion = 1, onTide, onTideClock }: Props) {
  const globe = useRef<GlobeMethods | undefined>(undefined)
  const [box, { width, height }] = useSize()
  const [hover, setHover] = useState<Country | null>(null)
  const texture = useMemo(() => weather ? weatherTexture(weather) : null, [weather])
  const mapMaterial = useMemo(() => texture
    ? new MeshPhongMaterial({ color: '#ffffff', map: texture, specular: '#000000', shininess: 0 })
    : greekMode ? new MeshPhongMaterial({ color: GREEK_PALETTE.ocean, specular: '#000000', shininess: 0 }) : OCEAN,
    [texture, greekMode])
  const land = greekMode ? GREEK_PALETTE.land : LAND
  const line = greekMode ? GREEK_PALETTE.line : LINE
  const accent = greekMode ? GREEK_PALETTE.accent : ACCENT
  const cap = useMemo(() => ({
    base: land,
    signal: tint(accent, 0.08, land),
    hover: tint(greekMode ? GREEK_PALETTE.hover : '#3d5560', 0.3, land),
    signalHover: tint(accent, 0.2, land),
  }), [land, accent, greekMode])
  const weatherCaps = useMemo(() => new MeshPhongMaterial({
    color: land,
    transparent: true,
    opacity: 0.16,
    depthWrite: false,
  }), [land])
  const weatherCapMaterial = useCallback(() => weatherCaps, [weatherCaps])
  useEffect(() => () => {
    texture?.dispose()
    if (mapMaterial !== OCEAN) mapMaterial.dispose()
  }, [texture, mapMaterial])
  useEffect(() => () => weatherCaps.dispose(), [weatherCaps])
  const onSelectRef = useRef(onSelect)
  useEffect(() => {
    onSelectRef.current = onSelect
  })

  const counts = useMemo(() => {
    const m = new Map<string, number>()
    events.forEach((e) => m.set(e.country, (m.get(e.country) ?? 0) + 1))
    return m
  }, [events])

  const ready = width > 0
  useEffect(() => {
    const g = globe.current
    if (!g || !ready) return
    g.renderer().setPixelRatio(Math.min(devicePixelRatio, 1.5)) // retina at 2-3x costs 2-4x the fill rate
    const controls = g.controls()
    controls.autoRotate = ambient && !matchMedia('(prefers-reduced-motion: reduce)').matches
    controls.autoRotateSpeed = 0.35
    controls.enabled = !ambient
    controls.minDistance = 101 // globe radius is 100: let users zoom down to regional level
    g.pointOfView(ambient ? { lat: 30, lng: 140, altitude: 1.6 } : { lat: 37, lng: -97, altitude: 1.6 })
  }, [ambient, ready])

  // Corner view: centre on the selection, pulled back so its surroundings fit the small box; back to the US view when cleared.
  const focusKey = focusAt ? `${focusAt.lat.toFixed(2)},${focusAt.lng.toFixed(2)}` : ''
  useEffect(() => {
    if (!ready || ambient) return
    const [lat, lng] = focusKey ? focusKey.split(',').map(Number) : [37, -97]
    const now = globe.current?.pointOfView().altitude ?? 2.2
    globe.current?.pointOfView({ lat, lng, altitude: focusKey ? (now <= DEEP_ALTITUDE ? now : 2.2) : 1.6 }, 800) // keep a deep zoom
  }, [focusKey, ready, ambient])

  const pip = useCallback((d: object) => {
    const e = d as SignalEvent
    const el = document.createElement('button')
    el.className = `pip ${e.trend}${e.commodity ? ' commodity' : ''}`
    el.setAttribute('aria-label', `${e.kicker}: ${e.title}, ${e.value}`)
    el.innerHTML = `<span class="pip-mark" aria-hidden="true"></span><span class="pip-tag"></span>`
    el.lastElementChild!.textContent = `${e.kicker} ${e.value}` // textContent: names come from the database
    el.onclick = () => onSelectRef.current?.(e)
    return el
  }, [])

  // Stable accessors: globe.gl re-walks every polygon whenever one of these changes identity.
  const capColor = useCallback(
    (d: object) => {
      const c = d as Country
      const signal = counts.has(c.id)
      if (c === hover) return signal ? cap.signalHover : cap.hover
      return signal ? cap.signal : cap.base
    },
    [counts, hover, cap],
  )
  const strokeColor = useCallback(
    (d: object) => (d === hover || (d as Country).id === selected?.country ? accent : line),
    [hover, selected, accent, line],
  )
  const label = useCallback(
    (d: object) => {
      const c = d as Country
      const n = counts.get(c.id) ?? 0
      const el = document.createElement('div')
      el.className = 'tag'
      el.textContent = `${c.properties.name}${n ? ` · ${n} signal${n > 1 ? 's' : ''}` : ''}`
      return el.outerHTML
    },
    [counts],
  )
  const onHover = useCallback((d: object | null) => setHover(d as Country | null), [])
  const onCountryRef = useRef(onCountry)
  useEffect(() => {
    onCountryRef.current = onCountry
  })
  const onClick = useCallback(
    (d: object, _e: MouseEvent, at: { lat: number; lng: number }) =>
      onCountryRef.current?.((d as Country).properties.name, at, (globe.current?.pointOfView().altitude ?? 9) <= DEEP_ALTITUDE),
    [],
  )
  const onShipRef = useRef(onShip)
  useEffect(() => {
    onShipRef.current = onShip
  })
  const onPlaneRef = useRef(onPlane)
  useEffect(() => {
    onPlaneRef.current = onPlane
  })
  const shipLayer = useRef<ShipLayer | null>(null)
  const [shipTip, setShipTip] = useState<{ x: number; y: number; text: string } | null>(null)
  useEffect(() => {
    const g = globe.current
    if (!g || !ready || ambient) return
    shipLayer.current = new ShipLayer(g, greekMode ? TRIREME_STYLE : SHIP_STYLE)
    return () => {
      shipLayer.current?.dispose()
      shipLayer.current = null
    }
  }, [ready, ambient, greekMode])
  useEffect(() => shipLayer.current?.setShips(ships), [ships, ready, greekMode])
  const onTideRef = useRef(onTide)
  const onTideClockRef = useRef(onTideClock)
  useEffect(() => {
    onTideRef.current = onTide
    onTideClockRef.current = onTideClock
  })
  const tideLayer = useRef<TideLayer | null>(null)
  useEffect(() => {
    const g = globe.current
    if (!g || !ready || ambient) return
    const layer = new TideLayer(g)
    layer.onClock = (ms) => onTideClockRef.current?.(ms)
    tideLayer.current = layer
    return () => {
      layer.dispose()
      tideLayer.current = null
    }
  }, [ready, ambient])
  useEffect(() => tideLayer.current?.setStations(tides), [tides, ready, ambient])
  useEffect(() => tideLayer.current?.setMotion(tideMotion), [tideMotion, ready, ambient])
  // Planes: passenger and cargo as two point layers (one icon colour each), always real time.
  const planeLayers = useRef<ShipLayer[]>([])
  useEffect(() => {
    const g = globe.current
    if (!g || !ready || ambient) return
    planeLayers.current = [new ShipLayer(g, PLANE_STYLE), new ShipLayer(g, CARGO_PLANE_STYLE)]
    return () => {
      planeLayers.current.forEach((l) => l.dispose())
      planeLayers.current = []
    }
  }, [ready, ambient])
  useEffect(() => {
    const [passenger, cargo] = planeLayers.current
    passenger?.setShips(planes.filter((p) => !p.cargo))
    cargo?.setShips(planes.filter((p) => p.cargo))
  }, [planes, ready])
  useEffect(() => shipLayer.current?.setMotion(motion), [motion, ready, greekMode])
  useEffect(() => {
    const g = globe.current
    if (!g || !ready || ambient || !windAndSurf || !weather || !marine) return
    const overlay = new WeatherOverlay(g, weather, marine)
    return () => overlay.dispose()
  }, [ready, ambient, windAndSurf, weather, marine])
  const local = (e: { clientX: number; clientY: number; currentTarget: HTMLElement }) => {
    const r = e.currentTarget.getBoundingClientRect()
    return [e.clientX - r.left, e.clientY - r.top] as const
  }
  // Ships and planes are drawn outside globe.gl, so their picking wins over the country underneath.
  const onShipClickCapture = (e: ReactMouseEvent<HTMLDivElement>) => {
    const hit = shipLayer.current?.pick(...local(e))
    if (hit) {
      e.stopPropagation()
      onShipRef.current?.(hit.mmsi)
      return
    }
    const plane = planeLayers.current.map((l) => l.pick(...local(e))).find(Boolean) as PlaneDot | undefined
    if (plane) {
      e.stopPropagation()
      onPlaneRef.current?.(plane.hex)
      return
    }
    const tide = tideLayer.current?.pick(...local(e))
    if (tide) {
      e.stopPropagation()
      onTideRef.current?.(tide.station.id)
    }
  }
  const onShipPointer = (e: ReactPointerEvent<HTMLDivElement>) => {
    const [x, y] = local(e)
    const hit = shipLayer.current?.pick(x, y)
    const plane = hit ? null : planeLayers.current.map((l) => l.pick(x, y)).find(Boolean)
    const tide = hit || plane ? null : tideLayer.current?.pick(x, y)
    e.currentTarget.style.cursor = hit || plane || tide ? 'pointer' : ''
    if (tide) {
      const { station, state } = tide
      const where = station.state ? `${station.name}, ${station.state}` : station.name
      return setShipTip({ x, y, text: state
        ? `${where} · ${state.level.toFixed(2)} m ${state.rising ? '▲ rising' : '▼ falling'} · ${state.next.type === 'H' ? 'high' : 'low'} ${fmtUtc(state.next.t)}`
        : `${where} · tide gauge` })
    }
    if (plane) return setShipTip({ x, y, text: `✈ ${plane.name ?? 'Unknown flight'} · ${plane.category}` })
    setShipTip(hit ? { x, y, text: `${hit.name ?? `MMSI ${hit.mmsi}`} · ${hit.category}${hit.speed_kn ? ` · ${hit.speed_kn} kn` : ''}` } : null)
  }
  const trackPaths = useMemo(() => (track.length > 1 ? [track] : []), [track])

  return (
    <div ref={box} className="earth" onClickCapture={ambient ? undefined : onShipClickCapture} onPointerMove={ambient ? undefined : onShipPointer}>
      {ready && (
        <Globe
          ref={globe}
          width={width}
          height={height}
          rendererConfig={RENDERER}
          backgroundColor="rgba(0,0,0,0)"
          globeMaterial={mapMaterial}
          atmosphereColor={greekMode ? GREEK_PALETTE.accent : '#4a6670'}
          atmosphereAltitude={0.08}
          polygonsData={COUNTRIES}
          polygonCapColor={capColor}
          polygonCapMaterial={texture ? weatherCapMaterial : undefined}
          polygonSideColor={noSides}
          polygonStrokeColor={strokeColor}
          polygonAltitude={0.006}
          polygonsTransitionDuration={0}
          polygonLabel={ambient ? undefined : label}
          onPolygonHover={ambient ? undefined : onHover}
          onPolygonClick={ambient ? undefined : onClick}
          htmlElementsData={events}
          htmlElement={pip}
          htmlAltitude={0.03}
          htmlElementVisibilityModifier={showWhenFacing}
          pathsData={trackPaths}
          pathPoints={(d: object) => d as TrackPoint[]}
          pathPointLat="lat"
          pathPointLng="lon"
          pathPointAlt={0.008}
          pathColor={() => SHIP}
          pathStroke={2}
          pathTransitionDuration={0}
        />
      )}
      {shipTip && (
        <div className="tag ship-tip" style={{ left: shipTip.x + 14, top: shipTip.y - 10 }}>{shipTip.text}</div>
      )}
    </div>
  )
}
