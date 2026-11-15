import { BufferAttribute, BufferGeometry, DynamicDrawUsage, LineSegments, ShaderMaterial } from 'three'
import type { GlobeMethods } from 'react-globe.gl'

/** The u/v/speed grid this needs (a subset of weatherLayer's WeatherGrid). */
export type WindField = {
  lats: number[]
  lons: number[]
  wind_u: (number | null)[]
  wind_v: (number | null)[]
  wind_ms: (number | null)[]
}

// Desktop keeps the full 5000; smaller viewports get proportionally fewer (the CPU step and the
// per-frame buffer upload both scale with this).
const PARTICLES = typeof innerWidth === 'number'
  ? Math.round(Math.min(5000, Math.max(1500, (innerWidth * innerHeight) / 400)))
  : 5000
const TRAIL = 18 // points per trail, so 17 segments
const SEGMENTS = TRAIL - 1
const DEG_PER_MS = 0.022 // degrees moved per frame for each m/s of wind (60 fps)
const ALT = 0.012
const MAX_LAT = 78
const RAD = Math.PI / 180

/** The field as flat Float32 grids, built once: NaN u marks a gap, s already falls back to |(u, v)|. */
export type PackedWind = { lat0: number; dLat: number; rows: number; lon0: number; dLon: number; cols: number; u: Float32Array; v: Float32Array; s: Float32Array }

export function packWind(field: WindField): PackedWind {
  const { lats, lons } = field
  const n = lats.length * lons.length
  const u = new Float32Array(n)
  const v = new Float32Array(n)
  const s = new Float32Array(n)
  for (let i = 0; i < n; i++) {
    const uu = field.wind_u[i]
    const vv = field.wind_v[i]
    const ss = field.wind_ms[i]
    const ok = uu != null && vv != null && Number.isFinite(uu) && Number.isFinite(vv)
    u[i] = ok ? uu : NaN
    v[i] = ok ? vv : NaN
    s[i] = ok ? (ss != null && Number.isFinite(ss) ? ss : Math.hypot(uu, vv)) : NaN
  }
  return { lat0: lats[0], dLat: lats[1] - lats[0], rows: lats.length, lon0: lons[0], dLon: lons[1] - lons[0], cols: lons.length, u, v, s }
}

/** Allocation-free bilinear wind into out[0..2] = u, v, speed, wrapping in longitude. false off-grid or on gaps. */
export function sampleWindInto(g: PackedWind, lat: number, lon: number, out: Float32Array | number[]): boolean {
  const { rows, cols } = g
  if (rows < 2 || cols < 2) return false
  const y = (lat - g.lat0) / g.dLat
  if (!(y >= 0 && y <= rows - 1)) return false
  const x = ((((lon - g.lon0) / g.dLon) % cols) + cols) % cols
  const r0 = Math.min(Math.floor(y), rows - 2)
  const c0 = Math.floor(x)
  const c1 = (c0 + 1) % cols
  const fy = y - r0
  const fx = x - c0
  const i00 = r0 * cols + c0
  const i01 = r0 * cols + c1
  const i10 = i00 + cols
  const i11 = i01 + cols
  const w00 = (1 - fx) * (1 - fy)
  const w01 = fx * (1 - fy)
  const w10 = (1 - fx) * fy
  const w11 = fx * fy
  const { u, v, s } = g
  const uu = u[i00] * w00 + u[i01] * w01 + u[i10] * w10 + u[i11] * w11
  if (Number.isNaN(uu)) return false // NaN at any corner poisons the sum, so this catches gaps
  out[0] = uu
  out[1] = v[i00] * w00 + v[i01] * w01 + v[i10] * w10 + v[i11] * w11
  out[2] = s[i00] * w00 + s[i01] * w01 + s[i10] * w10 + s[i11] * w11
  return true
}

/** Bilinear wind at any point of a regular lat/lon grid, wrapping in longitude. null off-grid or on gaps. */
export function sampleWind(field: WindField, lat: number, lon: number): [number, number, number] | null {
  const out: [number, number, number] = [0, 0, 0]
  return sampleWindInto(packWind(field), lat, lon, out) ? out : null
}

// Positions are the trail ring itself (no per-frame copy): each vertex knows its ring slot, and its
// age behind the head comes from uHead, so only xyz, speed and the life fade are re-uploaded. The one
// segment per ring that joins the newest point to the oldest is discarded. Faster air -> particles
// cover more ground per frame (so trails stretch), and the colour runs teal -> white -> storm amber.
const vertex = /* glsl */ `
  attribute float slot;
  attribute float life;
  attribute float speed;
  uniform float uHead;
  varying float vAlpha;
  varying float vSpeed;
  varying float vSeam;
  void main() {
    float age = mod(uHead - slot + ${TRAIL}.0, ${TRAIL}.0);
    vAlpha = life * (1.0 - age / ${SEGMENTS}.0);
    vSeam = (age < 0.5 || age > ${SEGMENTS}.0 - 0.5) ? 1.0 : 0.0;
    vSpeed = speed;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }`
const fragment = /* glsl */ `
  varying float vAlpha;
  varying float vSpeed;
  varying float vSeam;
  void main() {
    if (vSeam > 0.99) discard; // both ends are newest/oldest: the ring's wrap-around segment
    vec3 calm = vec3(0.36, 0.66, 0.78);
    vec3 fresh = vec3(0.86, 0.96, 1.0);
    vec3 storm = vec3(1.0, 0.64, 0.36);
    vec3 color = mix(calm, fresh, smoothstep(2.0, 12.0, vSpeed));
    color = mix(color, storm, smoothstep(15.0, 25.0, vSpeed));
    float strength = 0.5 + 0.5 * smoothstep(1.0, 14.0, vSpeed);
    gl_FragColor = vec4(color, vAlpha * strength);
  }`

/**
 * Wind as a swirling particle flow: particles drift along the interpolated wind field, each leaving a
 * fading trail, and respawn at random after a short life, so the field reads as moving streamlines
 * that curl around highs and lows.
 */
export class WindFlow {
  readonly object: LineSegments
  private readonly geometry = new BufferGeometry()
  private readonly material: ShaderMaterial
  private readonly field: PackedWind
  private readonly radius: number // globe radius at ALT, so positions skip globe.getCoords per particle
  private readonly wind = new Float32Array(3)
  private readonly lat = new Float32Array(PARTICLES)
  private readonly lon = new Float32Array(PARTICLES)
  private readonly age = new Float32Array(PARTICLES)
  private readonly life = new Float32Array(PARTICLES)
  private readonly trail = new Float32Array(PARTICLES * TRAIL * 3) // ring of xyz per particle
  private readonly trailSpeed = new Float32Array(PARTICLES * TRAIL)
  private readonly lifeFade = new Float32Array(PARTICLES * TRAIL) // per vertex, same for a whole trail
  private head = 0
  private last = 0

  constructor(globe: GlobeMethods, field: WindField) {
    this.field = packWind(field)
    this.radius = globe.getCoords(90, 0, ALT).y // the north pole sits at (0, r, 0)
    const slot = new Float32Array(PARTICLES * TRAIL)
    const index = new Uint32Array(PARTICLES * TRAIL * 2)
    for (let p = 0; p < PARTICLES; p++) {
      for (let k = 0; k < TRAIL; k++) {
        const i = p * TRAIL + k
        slot[i] = k
        index[i * 2] = i
        index[i * 2 + 1] = p * TRAIL + ((k + 1) % TRAIL)
      }
    }
    this.geometry.setIndex(new BufferAttribute(index, 1))
    this.geometry.setAttribute('slot', new BufferAttribute(slot, 1))
    this.geometry.setAttribute('position', new BufferAttribute(this.trail, 3).setUsage(DynamicDrawUsage))
    this.geometry.setAttribute('speed', new BufferAttribute(this.trailSpeed, 1).setUsage(DynamicDrawUsage))
    this.geometry.setAttribute('life', new BufferAttribute(this.lifeFade, 1).setUsage(DynamicDrawUsage))
    this.material = new ShaderMaterial({
      uniforms: { uHead: { value: 0 } },
      vertexShader: vertex,
      fragmentShader: fragment,
      transparent: true,
      depthTest: true,
      depthWrite: false,
    })
    this.object = new LineSegments(this.geometry, this.material)
    this.object.frustumCulled = false
    this.object.renderOrder = 3
    for (let p = 0; p < PARTICLES; p++) {
      this.spawn(p)
      this.age[p] = Math.random() * this.life[p] // stagger lives so respawns don't pulse
    }
  }

  /** Same math as globe.gl's getCoords (three-globe polar2Cartesian), written straight into the trail ring. */
  private place(p: number, k: number, lat: number, lon: number) {
    const phi = (90 - lat) * RAD
    const theta = (90 - lon) * RAD
    const sin = Math.sin(phi)
    const i = (p * TRAIL + k) * 3
    this.trail[i] = this.radius * sin * Math.cos(theta)
    this.trail[i + 1] = this.radius * Math.cos(phi)
    this.trail[i + 2] = this.radius * sin * Math.sin(theta)
  }

  private spawn(p: number) {
    // Uniform over the sphere's area between the poles' caps, which the grid does not cover.
    const limit = Math.sin(MAX_LAT * RAD)
    this.lat[p] = Math.asin((Math.random() * 2 - 1) * limit) / RAD
    this.lon[p] = Math.random() * 360 - 180
    this.age[p] = 0
    this.life[p] = 70 + Math.random() * 140
    this.place(p, 0, this.lat[p], this.lon[p])
    const s = sampleWindInto(this.field, this.lat[p], this.lon[p], this.wind) ? this.wind[2] : 0
    const base = p * TRAIL * 3
    this.trailSpeed[p * TRAIL] = s
    for (let k = 1; k < TRAIL; k++) {
      this.trail.copyWithin(base + k * 3, base, base + 3) // zero-length trail until it moves
      this.trailSpeed[p * TRAIL + k] = s
    }
  }

  /** Advance every particle by `frames` 60-fps frames and flag the changed buffers for upload. */
  step(frames = 1) {
    this.head = (this.head + 1) % TRAIL
    const w = this.wind
    const move = DEG_PER_MS * frames
    for (let p = 0; p < PARTICLES; p++) {
      const ok = sampleWindInto(this.field, this.lat[p], this.lon[p], w)
      this.age[p] += frames
      if (!ok || this.age[p] > this.life[p] || w[0] * w[0] + w[1] * w[1] < 0.09) { // |wind| < 0.3 m/s
        this.spawn(p)
        continue
      }
      const lat = this.lat[p] + w[1] * move
      if (Math.abs(lat) > MAX_LAT) {
        this.spawn(p)
        continue
      }
      const cos = Math.max(0.2, Math.cos(lat * RAD))
      this.lat[p] = lat
      this.lon[p] = ((this.lon[p] + (w[0] * move) / cos + 540) % 360) - 180
      this.place(p, this.head, lat, this.lon[p])
      this.trailSpeed[p * TRAIL + this.head] = w[2]
    }
    this.rebuild()
  }

  /** Time-based step for requestAnimationFrame; caps the catch-up after a hidden tab. */
  tick(time: number) {
    const frames = this.last ? Math.min(3, (time - this.last) / (1000 / 60)) : 1
    this.last = time
    this.step(frames)
  }

  private rebuild() {
    for (let p = 0; p < PARTICLES; p++) {
      // Fade in over the first frames of a life and out over the last, so particles never pop.
      const fade = Math.max(0, Math.min(1, this.age[p] / 12, (this.life[p] - this.age[p]) / 20))
      this.lifeFade.fill(fade, p * TRAIL, (p + 1) * TRAIL)
    }
    this.material.uniforms.uHead.value = this.head
    for (const name of ['position', 'speed', 'life']) this.geometry.getAttribute(name).needsUpdate = true
  }

  dispose() {
    this.geometry.dispose()
    this.material.dispose()
  }
}
