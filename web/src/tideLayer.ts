import type { GlobeMethods } from 'react-globe.gl'
import { BufferAttribute, BufferGeometry, Points, ShaderMaterial, Vector3 } from 'three'

/** One predicted high or low from GET /api/tides (tides.py). Heights in metres above MLLW. */
export type TideEvent = { t: string; v: number; type?: 'H' | 'L' }
export type TideStation = {
  id: string
  name: string
  state: string | null
  lat: number
  lon: number
  tide_type: string | null
  events: TideEvent[]
}
export type TideOverview = { stations: TideStation[]; fetched_at: string; window: { start: string; end: string } }

export type TideState = {
  level: number // metres above MLLW
  fill: number // 0 at the surrounding low, 1 at the surrounding high
  rising: boolean
  flow: number // |rate| relative to this tide's peak rate: 0 at slack water, 1 at mid-tide
  rate: number // metres per hour, signed
  next: TideEvent
}

const parsed = new WeakMap<TideEvent[], number[]>()
const times = (events: TideEvent[]) => {
  let ms = parsed.get(events)
  if (!ms) parsed.set(events, (ms = events.map((e) => Date.parse(e.t))))
  return ms
}

/** Level between two predicted turns follows a half cosine (how NOAA interpolates its tables). */
export function tideAt(events: TideEvent[], at: number): TideState | null {
  const ms = times(events)
  for (let i = 0; i < events.length - 1; i++) {
    const ta = ms[i]
    const tb = ms[i + 1]
    if (at < ta || at > tb || tb <= ta) continue
    const a = events[i]
    const b = events[i + 1]
    const f = (at - ta) / (tb - ta)
    const eased = (1 - Math.cos(Math.PI * f)) / 2
    const rising = b.v > a.v
    const hours = (tb - ta) / 3_600_000
    return {
      level: a.v + (b.v - a.v) * eased,
      fill: rising ? eased : 1 - eased,
      rising,
      flow: Math.sin(Math.PI * f),
      rate: ((b.v - a.v) * Math.PI / 2) * Math.sin(Math.PI * f) / hours,
      next: b,
    }
  }
  return null
}

/** Tidal range (m) over a station's whole prediction window: sets each gauge's size. */
export const tideRange = (events: TideEvent[]) =>
  events.length ? Math.max(...events.map((e) => e.v)) - Math.min(...events.map((e) => e.v)) : 0

// Each station is a small round gauge. The water inside fills to the current level between the
// surrounding low and high, with a surface that ripples harder as the tide runs faster. A ring
// spreads outward on the flood and draws inward on the ebb; at slack water it fades out. Flood is
// the wave layer's teal, ebb a deeper blue, so direction reads from colour as well as motion.
const vertex = /* glsl */ `
  attribute float fill;
  attribute float flow;
  attribute float rising;
  attribute float range;
  attribute float phase;
  uniform float pixelRatio;
  varying float vFill;
  varying float vFlow;
  varying float vRising;
  varying float vPhase;
  void main() {
    vFill = fill;
    vFlow = flow;
    vRising = rising;
    vPhase = phase;
    gl_PointSize = (16.0 + clamp(range, 0.0, 6.0) * 2.2) * pixelRatio;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }`
const fragment = /* glsl */ `
  uniform float uTime;
  uniform float uAnimate;
  varying float vFill;
  varying float vFlow;
  varying float vRising;
  varying float vPhase;
  const float R = 0.33; // gauge radius within the sprite; the ring travels between R and 0.5
  void main() {
    vec2 p = gl_PointCoord - 0.5;
    p.y = -p.y; // gl_PointCoord runs top-down
    float r = length(p);
    vec3 flood = vec3(0.30, 0.80, 0.82);
    vec3 ebb = vec3(0.24, 0.52, 0.86);
    vec3 water = mix(ebb, flood, vRising);

    float inside = 1.0 - smoothstep(R - 0.02, R, r);
    float surface = -R + 2.0 * R * vFill
      + sin(p.x * 38.0 + uTime * (1.5 + 4.0 * vFlow) + vPhase * 6.28) * 0.018 * (0.3 + vFlow) * uAnimate;
    float wet = inside * (1.0 - smoothstep(surface - 0.012, surface + 0.012, p.y));
    float crest = inside * (1.0 - smoothstep(0.0, 0.025, abs(p.y - surface)));
    float rim = 1.0 - smoothstep(0.012, 0.04, abs(r - R));

    float t = fract(uTime * (0.25 + 0.5 * vFlow) + vPhase);
    float travel = mix(1.0 - t, t, vRising); // outward on the flood, inward on the ebb
    float radius = R + 0.15 * travel;
    float ring = (1.0 - smoothstep(0.0, 0.03, abs(r - radius))) * (1.0 - travel * 0.85) * vFlow * uAnimate;

    vec3 color = water * (0.55 + 0.45 * wet) + vec3(0.85, 0.97, 1.0) * crest * 0.6;
    color = mix(color, water, ring * (1.0 - inside));
    color = mix(color, vec3(0.86, 0.95, 0.98), rim * 0.85); // the gauge outline always reads
    float alpha = max(max(wet * 0.92, inside * 0.25), max(rim * 0.9, ring * 0.75));
    alpha = max(alpha, crest * 0.95);
    if (alpha < 0.01) discard;
    gl_FragColor = vec4(color, alpha);
  }`

/** NOAA tide stations as live gauges on the globe; one draw call, picked in screen space. */
export class TideLayer {
  private stations: TideStation[] = []
  private states: (TideState | null)[] = []
  private readonly geometry = new BufferGeometry()
  private readonly material: ShaderMaterial
  private readonly points: Points
  private readonly globe: GlobeMethods
  private frame = 0
  private screen = new Float32Array(0)
  private sizes = new Float32Array(0)
  private motion = 1
  private startReal = performance.now()
  private startClock = Date.now()
  private lastClock = 0
  private lastEmit = 0 // performance.now() of the last onClock call
  private readonly motionQuery = matchMedia('(prefers-reduced-motion: reduce)')
  onClock?: (ms: number) => void

  constructor(globe: GlobeMethods) {
    this.globe = globe
    this.material = new ShaderMaterial({
      uniforms: {
        pixelRatio: { value: Math.min(devicePixelRatio, 1.5) },
        uTime: { value: 0 },
        uAnimate: { value: this.motionQuery.matches ? 0 : 1 },
      },
      vertexShader: vertex,
      fragmentShader: fragment,
      transparent: true,
      depthTest: true,
      depthWrite: false,
    })
    this.points = new Points(this.geometry, this.material)
    this.points.frustumCulled = false
    this.points.renderOrder = 5
    globe.scene().add(this.points)
    const tick = (time: number) => {
      this.update(time)
      this.frame = requestAnimationFrame(tick)
    }
    this.frame = requestAnimationFrame(tick)
  }

  setStations(stations: TideStation[]) {
    this.stations = stations
    const n = stations.length
    const positions = new Float32Array(n * 3)
    stations.forEach((s, i) => {
      const p = this.globe.getCoords(s.lat, s.lon, 0.02)
      positions.set([p.x, p.y, p.z], i * 3)
    })
    const ranges = Float32Array.from(stations, (s) => tideRange(s.events))
    this.sizes = Float32Array.from(ranges, (r) => 16 + Math.min(6, r) * 2.2)
    this.geometry.setAttribute('position', new BufferAttribute(positions, 3))
    this.geometry.setAttribute('range', new BufferAttribute(ranges, 1))
    this.geometry.setAttribute('phase', new BufferAttribute(Float32Array.from(stations, (_, i) => ((i * 61) % 89) / 89), 1))
    for (const name of ['fill', 'flow', 'rising']) this.geometry.setAttribute(name, new BufferAttribute(new Float32Array(n), 1))
    this.screen = new Float32Array(n * 2)
    this.states = []
    this.lastClock = 0
  }

  /** 1 = live. Above 1 the tide clock runs that many times faster, looping over the next 24 h. */
  setMotion(multiplier: number) {
    this.startClock = Date.now() // each time-lapse starts from now, so it stays inside the predictions
    this.startReal = performance.now()
    this.motion = multiplier
    this.lastClock = 0
  }

  clock() {
    if (this.motion === 1) return Date.now()
    const elapsed = (performance.now() - this.startReal) * this.motion
    return this.startClock + (elapsed % 86_400_000)
  }

  stateOf(id: string) {
    const i = this.stations.findIndex((s) => s.id === id)
    return i < 0 ? null : this.states[i] ?? null
  }

  private update(time: number) {
    this.material.uniforms.uTime.value = time * 0.001
    this.material.uniforms.uAnimate.value = this.motionQuery.matches ? 0 : 1
    if (!this.stations.length) return
    const now = this.clock()
    // Tides move slowly: live, recompute every 5 s; in time-lapse, every frame.
    if (this.motion !== 1 || now - this.lastClock > 5000) {
      const fill = this.geometry.getAttribute('fill') as BufferAttribute
      const flow = this.geometry.getAttribute('flow') as BufferAttribute
      const rising = this.geometry.getAttribute('rising') as BufferAttribute
      this.states = this.stations.map((s, i) => {
        const st = tideAt(s.events, now)
        fill.setX(i, st?.fill ?? 0.5)
        flow.setX(i, st?.flow ?? 0)
        rising.setX(i, st?.rising ? 1 : 0)
        return st
      })
      fill.needsUpdate = flow.needsUpdate = rising.needsUpdate = true
      this.lastClock = now
      if (time - this.lastEmit > 250) { // at most 4 React updates a second
        this.lastEmit = time
        this.onClock?.(now)
      }
    }
    const pos = this.geometry.getAttribute('position') as BufferAttribute
    const camera = this.globe.camera()
    const canvas = this.globe.renderer().domElement
    const v = new Vector3()
    for (let i = 0; i < this.stations.length; i++) {
      v.set(pos.getX(i), pos.getY(i), pos.getZ(i)).project(camera)
      this.screen[i * 2] = ((v.x + 1) / 2) * canvas.clientWidth
      this.screen[i * 2 + 1] = ((1 - v.y) / 2) * canvas.clientHeight
    }
  }

  /** The station whose gauge is under a CSS-pixel point (front of the globe only). */
  pick(x: number, y: number): { station: TideStation; state: TideState | null } | null {
    const pos = this.geometry.getAttribute('position') as BufferAttribute | undefined
    if (!pos) return null
    const cam = this.globe.camera().position
    const p = new Vector3()
    let best = -1
    let bestD = Infinity
    this.stations.forEach((_, i) => {
      const dx = this.screen[i * 2] - x
      const dy = this.screen[i * 2 + 1] - y
      const d = dx * dx + dy * dy
      const hit = (this.sizes[i] * 0.33 + 6) ** 2 // gauge radius plus a margin
      if (d >= hit || d >= bestD) return
      p.set(pos.getX(i), pos.getY(i), pos.getZ(i))
      if (p.dot(cam.clone().sub(p)) <= 0) return
      best = i
      bestD = d
    })
    return best < 0 ? null : { station: this.stations[best], state: this.states[best] ?? null }
  }

  dispose() {
    cancelAnimationFrame(this.frame)
    this.globe.scene().remove(this.points)
    this.geometry.dispose()
    this.material.dispose()
  }
}
