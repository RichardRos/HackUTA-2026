import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent, type PointerEvent as ReactPointerEvent } from 'react'
import Globe, { type GlobeMethods } from 'react-globe.gl'
import { MeshPhongMaterial, Vector3 } from 'three'
import { feature } from 'topojson-client'
import atlas from 'world-atlas/countries-110m.json'
import type { SignalEvent } from './events'
import { medallionSvg } from './greek/art'
import { blackShipTexture, greekOceanTexture, odysseusFix, SeaCreatureLayer } from './greek/globe'
import { ANCIENT_LANDS, greekNumeral, inOdyssey, isGreekMarker, MONSTERS, STATIONS, VOYAGE, type GreekMarker, type LoreEntry } from './greek/myth'
import { CARGO_PLANE_STYLE, LONGSHIP_STYLE, PLANE_STYLE, SHIP_STYLE, TRIREME_STYLE, ShipLayer, type LayerStyle, type MovingShip } from './shipLayer'
import { TideLayer, type TideStation } from './tideLayer'
import { WeatherOverlay, weatherTexture, type MarineGrid, type WeatherGrid } from './weatherLayer'

type Country = { id: string; properties: { name: string } }

const fmtUtc = (iso: string) => `${iso.slice(11, 16)} UTC`

const LAND = '#1a272d'
const LINE = '#2d3d43'
const ACCENT = '#7bf1b1'
const OCEAN = new MeshPhongMaterial({ color: '#0e1820', specular: '#000000', shininess: 0 })
const DEEP_ALTITUDE = 0.55 // zoomed in this far, a click means a region rather than the whole country
const GREEK_PALETTE = { land: '#e6d7bb', line: '#8b6d4c', accent: '#e8c97a', edge: '#a0432a', hover: '#c45c3e' }
const HELLAS = '300' // Greece, gilded on the Greek-mode map
const RENDERER = { antialias: true, alpha: true, powerPreference: 'high-performance' as const }

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const toCountries = (topo: any) => (feature(topo, topo.objects.countries) as any).features as Country[]
const COUNTRIES = toCountries(atlas)
/**
 * Greek mode zooms into the Aegean, where the 1:110m outlines lose the islands and blur the coasts,
 * so it swaps in the 1:50m map, loaded on demand so the standard view stays light.
 */
let detailedMap: Promise<Country[]> | null = null
const loadDetailedMap = () => (detailedMap ??= import('world-atlas/countries-50m.json').then((m) => toCountries(m.default)))

/** Blend `hex` over the land colour by `t` (0..1). */
function tint(hex: string, t: number, base = LAND) {
  const ch = (c: string, i: number) => parseInt(c.slice(1 + i * 2, 3 + i * 2), 16)
  return `rgb(${[0, 1, 2].map((i) => Math.round(ch(base, i) * (1 - t) + ch(hex, i) * t)).join(',')})`
}
const noSides = () => 'rgba(0,0,0,0)'
const showWhenFacing = (el: HTMLElement, visible: boolean) => {
  if (el.classList.contains('gm')) {
    // Greek markers fade by CSS, so the declutter pass can hide them too.
    const v = String(visible)
    if (el.dataset.facing !== v) el.dataset.facing = v
    return
  }
  el.style.opacity = visible ? '1' : '0'
  el.style.pointerEvents = visible ? 'auto' : 'none'
}

/* Greek-mode paths and rings. The accessors live at module level so globe.gl never re-digests them on hover. */
type PathDatum = { kind: 'track' | 'voyage' | 'voyage-glow' | 'route'; points: { lat: number; lon: number }[]; route?: RouteLine }
const VOYAGE_POINTS = VOYAGE.map((p) => ({ lat: p.lat, lon: p.lng }))
const VOYAGE_PATHS: PathDatum[] = [{ kind: 'voyage-glow', points: VOYAGE_POINTS }, { kind: 'voyage', points: VOYAGE_POINTS }]
const pathPoints = (d: object) => (d as PathDatum).points
const pathColor = (d: object) => {
  const { kind: k, route } = d as PathDatum
  if (route) return route.color
  return k === 'track' ? SHIP : k === 'voyage-glow' ? 'rgba(232,201,122,0.22)' : ['rgba(246,220,142,0.45)', '#f6dc8e']
}
// A planned route (voyage planner) carries its own look: dashed = shortest path, flow = animated toward the destination.
const routeOf = (d: object) => (d as PathDatum).route
const pathStroke = (d: object) => routeOf(d) ? routeOf(d)!.stroke ?? 2 : ({ track: 2, voyage: 1.8, 'voyage-glow': 5, route: 2 })[(d as PathDatum).kind]
const pathDash = (d: object) => {
  const r = routeOf(d)
  return r ? (r.dashed ? 0.01 : r.flow ? 0.06 : 1) : (d as PathDatum).kind === 'voyage' ? 0.011 : 1
}
const pathGap = (d: object) => {
  const r = routeOf(d)
  return r ? (r.dashed ? 0.008 : r.flow ? 0.004 : 0) : (d as PathDatum).kind === 'voyage' ? 0.007 : 0
}
const pathAnimate = (d: object) => {
  const r = routeOf(d)
  return r ? (r.flow ? 24000 : 0) : (d as PathDatum).kind === 'voyage' ? 90000 : 0
}

type RingDatum = { kind: 'charybdis' | 'hearth' | 'vessel'; lat: number; lng: number } // vessel: the planner's ship
const CHARYBDIS: RingDatum = { kind: 'charybdis', lat: 38.235, lng: 15.6 }
const HEARTH: RingDatum = { kind: 'hearth', lat: 38.43, lng: 20.67 } // the fire kept burning on Ithaca
const swirl = (t: number) => `rgba(170,225,235,${(Math.sin(Math.PI * t) * 0.85).toFixed(3)})`
const glow = (t: number) => `rgba(246,220,142,${(1 - t).toFixed(3)})`
const amber = (t: number) => `rgba(255,199,122,${(1 - t).toFixed(3)})`
const ringKind = (d: object) => (d as RingDatum).kind
const ringColor = (d: object) => ({ charybdis: swirl, hearth: glow, vessel: amber })[ringKind(d)]
const ringRadius = (d: object) => ({ charybdis: 1.1, hearth: 1.8, vessel: 2.2 })[ringKind(d)]
const ringSpeed = (d: object) => ({ charybdis: -0.75, hearth: 0.9, vessel: 2.5 })[ringKind(d)] // negative: the whirlpool pulls inward
const ringPeriod = (d: object) => ({ charybdis: 450, hearth: 1500, vessel: 700 })[ringKind(d)]

const ODYSSEUS_STYLE: LayerStyle = { texture: blackShipTexture, iconPx: 30, alt: 0.009 }
const GREEK_HOME = { lat: 36, lng: 17, altitude: 1.45 } // the Greek world, from Gibraltar to Colchis
const HOME = { lat: 37, lng: -97, altitude: 1.6 }

const rankOf = (d: GreekMarker, active: string | null) =>
  `${d.kind}:${d.id}` === active ? -1 : d.kind === 'monster' ? d.rank : d.id === 'ithaca' ? 0 : 4

/** A monster roundel or a voyage stop, built once per marker and toggled by class afterwards. */
function greekElement(d: GreekMarker, interactive: boolean, onPick: (d: GreekMarker) => void) {
  const el = document.createElement('div')
  el.className = `gm gm-${d.kind}${d.kind === 'station' && d.id === 'ithaca' ? ' gm-home' : ''}`
  const body = document.createElement(interactive ? 'button' : 'span')
  body.className = 'gm-body'
  if (interactive) {
    const b = body as HTMLButtonElement
    b.type = 'button'
    b.setAttribute('aria-label', d.kind === 'monster'
      ? `${d.name}, ${d.epithet}. Read the legend`
      : `Odyssey stop ${d.n} of ${STATIONS.length}: ${d.name}. Read the episode`)
    // Keep the press off the globe underneath, so it doesn't also count as a click on the map.
    b.addEventListener('pointerdown', (e) => e.stopPropagation())
    b.addEventListener('pointerup', (e) => e.stopPropagation())
    b.onclick = (e) => {
      e.stopPropagation()
      onPick(d)
    }
  }
  // All text here comes from the static myth tables, never from the API.
  body.innerHTML = d.kind === 'monster'
    ? `<span class="gm-halo"></span><span class="gm-art" style="animation-delay:-${((d.lat * 7.3) % 5).toFixed(2)}s">${medallionSvg(d.id, 'marker', inOdyssey(d) ? 'clay' : 'royal')}</span>`
      + `<span class="gm-label"><b>${d.name}</b><i lang="grc">${d.greek}</i></span>`
    : `<span class="gm-dot"></span><span class="gm-label"><b><span lang="grc">${greekNumeral(d.n)}</span> ${d.name}</b><i lang="grc">${d.greek}</i></span>`
  el.append(body)
  return el
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
  temperature?: boolean // paint the temperature/rain texture; off for Wind & Surf so the motion reads
  greekMode?: boolean
  tides?: TideStation[] // NOAA tide gauges; empty hides the layer
  tideMotion?: number // tide clock multiplier; 1 = live
  onTide?: (id: string) => void
  onTideClock?: (ms: number) => void // the tide clock, a few times a second in time-lapse
  showMonsters?: boolean // Greek mode: the bestiary and the sea creatures
  showVoyage?: boolean // Greek mode: Odysseus' route home
  onLore?: (entry: LoreEntry) => void
  lore?: LoreEntry | null // the open legend: the globe flies to it and keeps its marker on top
  routes?: RouteLine[] // planned voyages
  stops?: { lat: number; lon: number; name: string }[] // labelled waypoints (ports, Odyssey episodes)
  vessel?: { lat: number; lon: number } | null // the ship's position on the voyage timeline
  onPick?: (lat: number, lon: number) => void // set: clicks pick a point instead of a country
}

export type RouteLine = { points: { lat: number; lon: number }[]; color: string | string[]; dashed?: boolean; stroke?: number; flow?: boolean }

export type TrackPoint = { lat: number; lon: number }
export type ShipDot = MovingShip
export type PlaneDot = MovingShip & { cargo: boolean; hex: string } // name = callsign, category = tooltip detail

const NO_EVENTS: SignalEvent[] = []
const NO_SHIPS: ShipDot[] = []
const NO_PLANES: PlaneDot[] = []
const NO_TRACK: TrackPoint[] = []
const NO_TIDES: TideStation[] = []
const NO_ROUTES: RouteLine[] = []
const NO_STOPS: { lat: number; lon: number; name: string }[] = []
const SHIP = '#ffc77a' // the Figma's ship amber

export default function Earth({ events = NO_EVENTS, selected, onSelect, onCountry, ships = NO_SHIPS, onShip, planes = NO_PLANES, onPlane, motion = 1, track = NO_TRACK, focusAt = null, ambient = false, weather = null, marine = null, windAndSurf = false, temperature = true, greekMode = false, tides = NO_TIDES, tideMotion = 1, onTide, onTideClock, showMonsters = true, showVoyage = true, onLore, lore = null, routes = NO_ROUTES, stops = NO_STOPS, vessel = null, onPick }: Props) {
  const globe = useRef<GlobeMethods | undefined>(undefined)
  const [box, { width, height }] = useSize()
  const [hover, setHover] = useState<Country | null>(null)
  const [detailed, setDetailed] = useState<Country[] | null>(null)
  useEffect(() => {
    if (!greekMode || ambient || detailed) return
    let live = true
    loadDetailedMap().then((c) => live && setDetailed(c), () => {}) // on failure the 1:110m map simply stays
    return () => {
      live = false
    }
  }, [greekMode, ambient, detailed])
  const countries = greekMode && !ambient && detailed ? detailed : COUNTRIES
  // The temperature/rain texture only under the Weather layer, and never in Greek mode, which is sea-only:
  // its Wind & Surf overlay still uses `weather`, but the land temperature texture is skipped.
  const texture = useMemo(() => weather && temperature && !greekMode ? weatherTexture(weather) : null, [weather, temperature, greekMode])
  // In its place, the wine-dark sea: an old-chart ocean with waves, a gilt graticule and the seas' Greek names.
  const greekOcean = useMemo(() => greekMode ? greekOceanTexture() : null, [greekMode])
  useEffect(() => () => greekOcean?.dispose(), [greekOcean])
  const mapMaterial = useMemo(() => texture
    ? new MeshPhongMaterial({ color: '#ffffff', map: texture, specular: '#000000', shininess: 0 })
    : greekOcean ? new MeshPhongMaterial({ color: '#ffffff', map: greekOcean, specular: '#24465e', shininess: 7 }) : OCEAN,
    [texture, greekOcean])
  const land = greekMode ? GREEK_PALETTE.land : LAND
  const line = greekMode ? GREEK_PALETTE.line : LINE
  const accent = greekMode ? GREEK_PALETTE.accent : ACCENT
  const edge = greekMode ? GREEK_PALETTE.edge : ACCENT
  const cap = useMemo(() => ({
    base: land,
    signal: tint(accent, 0.08, land),
    hover: tint(greekMode ? GREEK_PALETTE.hover : '#3d5560', 0.3, land),
    signalHover: tint(accent, 0.2, land),
    hellas: tint('#d9a93f', 0.42, land),
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
  const onLoreRef = useRef(onLore)
  useEffect(() => {
    onLoreRef.current = onLore
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
    g.renderer().setPixelRatio(Math.min(devicePixelRatio, 2)) // sharp on 2x screens; 3x would cost over twice the fill rate for little visible gain
    const controls = g.controls()
    controls.autoRotate = ambient && !matchMedia('(prefers-reduced-motion: reduce)').matches
    controls.autoRotateSpeed = 0.35
    controls.enabled = !ambient
    controls.minDistance = 101 // globe radius is 100: let users zoom down to regional level
    g.pointOfView(ambient ? (greekMode ? { lat: 30, lng: 24, altitude: 1.6 } : { lat: 30, lng: 140, altitude: 1.6 }) : greekMode ? GREEK_HOME : HOME)
    // greekMode only picks the opening view; the effect below flies between the two homes afterwards.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ambient, ready])

  // Corner view: centre on the selection, pulled back so its surroundings fit the small box; back home when cleared.
  const focusKey = focusAt ? `${focusAt.lat.toFixed(2)},${focusAt.lng.toFixed(2)}` : ''
  useEffect(() => {
    if (!ready || ambient) return
    if (!focusKey) {
      globe.current?.pointOfView(greekMode ? GREEK_HOME : HOME, 800)
      return
    }
    const [lat, lng] = focusKey.split(',').map(Number)
    const now = globe.current?.pointOfView().altitude ?? 2.2
    globe.current?.pointOfView({ lat, lng, altitude: now <= DEEP_ALTITUDE ? now : 2.2 }, 800) // keep a deep zoom
  }, [focusKey, ready, ambient, greekMode])

  // Opening a legend flies the globe to its creature or stop.
  const loreKey = lore ? `${lore.kind}:${lore.id}` : null
  const loreRef = useRef(loreKey)
  useEffect(() => {
    loreRef.current = loreKey
  }, [loreKey])
  useEffect(() => {
    if (!ready || ambient || !lore) return
    globe.current?.pointOfView({ lat: lore.lat - 1.5, lng: lore.lng, altitude: lore.kind === 'monster' ? 0.95 : 0.8 }, 1400)
  }, [lore, ready, ambient])

  // A new voyage: fly to fit the whole route (centre of its points on the sphere, zoomed to its spread).
  const routeKey = routes.length ? routes.map((r) => `${r.points.length}:${r.points[0]?.lat},${r.points[0]?.lon}`).join('|') : ''
  useEffect(() => {
    if (!ready || ambient || !routeKey) return
    const pts = routes.flatMap((r) => r.points)
    const v = pts.reduce((acc, p) => {
      const la = (p.lat * Math.PI) / 180
      const lo = (p.lon * Math.PI) / 180
      return [acc[0] + Math.cos(la) * Math.cos(lo), acc[1] + Math.cos(la) * Math.sin(lo), acc[2] + Math.sin(la)]
    }, [0, 0, 0])
    const n = Math.hypot(...v) || 1
    const [x, y, z] = v.map((c) => c / n)
    const spread = Math.max(...pts.map((p) => {
      const la = (p.lat * Math.PI) / 180
      const lo = (p.lon * Math.PI) / 180
      return Math.acos(Math.min(1, Math.cos(la) * Math.cos(lo) * x + Math.cos(la) * Math.sin(lo) * y + Math.sin(la) * z))
    }))
    const altitude = Math.max(0.75, Math.min(2.6, spread * 3.2 + 0.35)) // ~0.9 for the Aegean, ~2 for an ocean
    globe.current?.pointOfView({ lat: (Math.asin(z) * 180) / Math.PI, lng: (Math.atan2(y, x) * 180) / Math.PI, altitude }, 1200)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- refit only when a different route arrives
  }, [routeKey, ready, ambient])

  const greekEls = useRef(new Map<string, { d: GreekMarker; body: HTMLElement }>())
  const pip = useCallback((d: object) => {
    if (isGreekMarker(d)) {
      const el = greekElement(d, !ambient, (m) => onLoreRef.current?.(m))
      greekEls.current.set(`${d.kind}:${d.id}`, { d, body: el.firstElementChild as HTMLElement })
      return el
    }
    const e = d as SignalEvent
    const el = document.createElement('button')
    el.className = `pip ${e.trend}${e.commodity ? ' commodity' : ''}`
    el.setAttribute('aria-label', `${e.kicker}: ${e.title}, ${e.value}`)
    el.innerHTML = `<span class="pip-mark" aria-hidden="true"></span><span class="pip-tag"></span>`
    el.lastElementChild!.textContent = `${e.kicker} ${e.value}` // textContent: names come from the database
    el.onclick = () => onSelectRef.current?.(e)
    return el
  }, [ambient])

  const greekMarkers = useMemo<GreekMarker[]>(() => greekMode
    ? [...(showMonsters ? MONSTERS : []), ...(showVoyage ? STATIONS : [])]
    : [], [greekMode, showMonsters, showVoyage])
  const htmlData = useMemo(() => [...events, ...greekMarkers], [events, greekMarkers])
  useEffect(() => {
    const keep = new Set(greekMarkers.map((d) => `${d.kind}:${d.id}`))
    for (const key of greekEls.current.keys()) if (!keep.has(key)) greekEls.current.delete(key)
  }, [greekMarkers])

  // Declutter: from the most famous down, a marker that would overlap one already shown waits until you zoom in.
  useEffect(() => {
    const host = box.current
    if (!ready || !greekMode || !host) return
    const v = new Vector3()
    const toCam = new Vector3()
    const id = window.setInterval(() => {
      const g = globe.current
      if (!g) return
      const alt = g.pointOfView().altitude
      const scale = Math.max(0.72, Math.min(1.25, 1.32 - 0.3 * alt))
      host.style.setProperty('--gm-scale', scale.toFixed(3))
      const near = String(alt < 0.62)
      if (host.dataset.near !== near) host.dataset.near = near
      const cam = g.camera().position
      const placed: [number, number, number][] = []
      const active = loreRef.current
      const items = [...greekEls.current.values()].sort((a, b) => rankOf(a.d, active) - rankOf(b.d, active))
      for (const { d, body } of items) {
        const p = g.getCoords(d.lat, d.lng, 0)
        v.set(p.x, p.y, p.z)
        if (v.dot(toCam.copy(cam).sub(v)) <= 0) continue // far side: the facing check already hides it
        const s = g.getScreenCoords(d.lat, d.lng, 0.03)
        const r = (d.kind === 'monster' ? 22 : 8) * scale
        const crowded = placed.some(([x, y, rr]) => (x - s.x) ** 2 + (y - s.y) ** 2 < (rr + r) ** 2)
        if (crowded !== body.classList.contains('crowded')) body.classList.toggle('crowded', crowded)
        const isActive = `${d.kind}:${d.id}` === active
        if (isActive !== body.classList.contains('active')) body.classList.toggle('active', isActive)
        if (!crowded) placed.push([s.x, s.y, r])
      }
    }, 140)
    return () => clearInterval(id)
  }, [ready, greekMode, box])

  // Stable accessors: globe.gl re-walks every polygon whenever one of these changes identity.
  const capColor = useCallback(
    (d: object) => {
      const c = d as Country
      const signal = counts.has(c.id)
      if (c === hover) return signal ? cap.signalHover : cap.hover
      if (greekMode && c.id === HELLAS) return cap.hellas
      return signal ? cap.signal : cap.base
    },
    [counts, hover, cap, greekMode],
  )
  const strokeColor = useCallback(
    (d: object) => (d === hover || (d as Country).id === selected?.country ? edge : line),
    [hover, selected, edge, line],
  )
  const label = useCallback(
    (d: object) => {
      const c = d as Country
      const n = counts.get(c.id) ?? 0
      const ancient = greekMode ? ANCIENT_LANDS[c.id] : undefined
      const el = document.createElement('div')
      el.className = 'tag'
      el.textContent = `${c.properties.name}${ancient ? ` · ${ancient}` : ''}${n ? ` · ${n} signal${n > 1 ? 's' : ''}` : ''}`
      return el.outerHTML
    },
    [counts, greekMode],
  )
  const onHover = useCallback((d: object | null) => setHover(d as Country | null), [])
  const onCountryRef = useRef(onCountry)
  useEffect(() => {
    onCountryRef.current = onCountry
  })
  const onPickRef = useRef(onPick)
  useEffect(() => {
    onPickRef.current = onPick
  })
  const onClick = useCallback(
    (d: object, _e: MouseEvent, at: { lat: number; lng: number }) => {
      if (onPickRef.current) return onPickRef.current(at.lat, at.lng)
      onCountryRef.current?.((d as Country).properties.name, at, (globe.current?.pointOfView().altitude ?? 9) <= DEEP_ALTITUDE)
    },
    [],
  )
  const onGlobeClick = useCallback(({ lat, lng }: { lat: number; lng: number }) => onPickRef.current?.(lat, lng), [])
  // Greek mode has no country briefings, but hovering a land still names it in Greek.
  const hoverLand = !ambient && (!!onCountry || greekMode)
  const onShipRef = useRef(onShip)
  useEffect(() => {
    onShipRef.current = onShip
  })
  const onPlaneRef = useRef(onPlane)
  useEffect(() => {
    onPlaneRef.current = onPlane
  })
  // Live AIS ships. Greek mode draws two layers: tankers sail as longships, every other vessel as a trireme.
  const shipLayers = useRef<ShipLayer[]>([])
  const [shipTip, setShipTip] = useState<{ x: number; y: number; text: string } | null>(null)
  useEffect(() => {
    const g = globe.current
    if (!g || !ready || ambient) return
    shipLayers.current = greekMode
      ? [new ShipLayer(g, TRIREME_STYLE), new ShipLayer(g, LONGSHIP_STYLE)]
      : [new ShipLayer(g, SHIP_STYLE)]
    return () => {
      shipLayers.current.forEach((l) => l.dispose())
      shipLayers.current = []
    }
  }, [ready, ambient, greekMode])
  useEffect(() => {
    const [main, tankers] = shipLayers.current
    if (tankers) {
      main.setShips(ships.filter((s) => s.category !== 'tanker'))
      tankers.setShips(ships.filter((s) => s.category === 'tanker'))
    } else main?.setShips(ships)
  }, [ships, ready, greekMode])
  const pickShipAt = (x: number, y: number) => shipLayers.current.map((l) => l.pick(x, y)).find(Boolean) ?? null
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
  useEffect(() => shipLayers.current.forEach((l) => l.setMotion(motion)), [motion, ready, greekMode])
  useEffect(() => {
    const g = globe.current
    if (!g || !ready || ambient || !windAndSurf || !weather || !marine) return
    const overlay = new WeatherOverlay(g, weather, marine)
    return () => overlay.dispose()
  }, [ready, ambient, windAndSurf, weather, marine])

  // Greek mode: ketea and hippocampi circling the open oceans.
  useEffect(() => {
    const g = globe.current
    if (!g || !ready || !greekMode || !showMonsters) return
    const layer = new SeaCreatureLayer(g)
    return () => layer.dispose()
  }, [ready, greekMode, showMonsters])
  // Greek mode: Odysseus' black ship sails the route on a loop, Troy to Ithaca.
  useEffect(() => {
    const g = globe.current
    if (!g || !ready || !greekMode || !showVoyage) return
    const ship = new ShipLayer(g, ODYSSEUS_STYLE)
    const t0 = performance.now()
    const sail = () => {
      const fix = odysseusFix((performance.now() - t0) / 1000)
      if (fix) ship.setShips([fix])
    }
    sail()
    const id = window.setInterval(sail, 400)
    return () => {
      clearInterval(id)
      ship.dispose()
    }
  }, [ready, greekMode, showVoyage])

  const local = (e: { clientX: number; clientY: number; currentTarget: HTMLElement }) => {
    const r = e.currentTarget.getBoundingClientRect()
    return [e.clientX - r.left, e.clientY - r.top] as const
  }
  // Ships, planes and tide gauges are drawn outside globe.gl, so their picking wins over the country underneath.
  const onShipClickCapture = (e: ReactMouseEvent<HTMLDivElement>) => {
    if ((e.target as HTMLElement).closest('.gm')) return // a monster or a voyage stop takes the click
    const hit = pickShipAt(...local(e))
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
    const hit = pickShipAt(x, y)
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
  const paths = useMemo<PathDatum[]>(() => [
    ...(track.length > 1 ? [{ kind: 'track' as const, points: track }] : []),
    ...(greekMode && showVoyage ? VOYAGE_PATHS : []),
    ...routes.map((route) => ({ kind: 'route' as const, points: route.points, route })),
  ], [track, greekMode, showVoyage, routes])
  const rings = useMemo<RingDatum[]>(() => [
    ...(greekMode ? [...(showMonsters ? [CHARYBDIS] : []), ...(showVoyage ? [HEARTH] : [])] : []),
    ...(vessel ? [{ kind: 'vessel' as const, lat: vessel.lat, lng: vessel.lon }] : []),
  ], [greekMode, showMonsters, showVoyage, vessel])

  return (
    <div ref={box} className={`earth${onPick ? ' picking' : ''}`} onClickCapture={ambient || onPick ? undefined : onShipClickCapture} onPointerMove={ambient || onPick ? undefined : onShipPointer}>
      {ready && (
        <Globe
          ref={globe}
          width={width}
          height={height}
          rendererConfig={RENDERER}
          backgroundColor="rgba(0,0,0,0)"
          globeMaterial={mapMaterial}
          atmosphereColor={greekMode ? GREEK_PALETTE.accent : '#4a6670'}
          atmosphereAltitude={greekMode ? 0.13 : 0.08}
          polygonsData={countries}
          polygonCapColor={capColor}
          polygonCapMaterial={texture ? weatherCapMaterial : undefined}
          polygonSideColor={noSides}
          polygonStrokeColor={strokeColor}
          polygonAltitude={0.006}
          polygonsTransitionDuration={0}
          polygonLabel={hoverLand ? label : undefined}
          onPolygonHover={hoverLand ? onHover : undefined}
          onPolygonClick={ambient || (!onCountry && !onPick) ? undefined : onClick}
          htmlElementsData={htmlData}
          htmlElement={pip}
          htmlAltitude={0.03}
          htmlElementVisibilityModifier={showWhenFacing}
          pathsData={paths}
          pathPoints={pathPoints}
          pathPointLat="lat"
          pathPointLng="lon"
          pathPointAlt={0.008}
          pathColor={pathColor}
          pathStroke={pathStroke}
          pathDashLength={pathDash}
          pathDashGap={pathGap}
          pathDashAnimateTime={pathAnimate}
          pathTransitionDuration={0}
          ringsData={rings}
          ringColor={ringColor}
          ringMaxRadius={ringRadius}
          ringPropagationSpeed={ringSpeed}
          ringRepeatPeriod={ringPeriod}
          ringAltitude={0.007}
          labelsData={stops}
          labelLat="lat"
          labelLng="lon"
          labelText="name"
          labelSize={0.28}
          labelDotRadius={0.16}
          labelColor={() => (greekMode ? '#f6eddf' : '#e6ecee')}
          labelAltitude={0.012}
          labelResolution={2}
          labelsTransitionDuration={0}
          onGlobeClick={ambient ? undefined : onGlobeClick}
        />
      )}
      {shipTip && (
        <div className="tag ship-tip" style={{ left: shipTip.x + 14, top: shipTip.y - 10 }}>{shipTip.text}</div>
      )}
    </div>
  )
}
